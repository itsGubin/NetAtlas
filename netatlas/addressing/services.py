"""VLANs and Subnets. The Subnet tree is derived from the addresses, so it can never go stale."""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import ip_network

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.addressing.models import Subnet, Vlan
from netatlas.history.services import create_item, delete_item, update_item

_UPDATABLE_FIELDS = {"cidr", "site_id", "vlan_id"}
_UPDATABLE_VLAN_FIELDS = {"number", "name"}


@dataclass(frozen=True)
class VlanSummary:
    id: int
    number: int
    name: str
    site_id: int
    site_name: str


@dataclass(frozen=True)
class VlanSubnet:
    id: int
    cidr: str


@dataclass(frozen=True)
class VlanInterface:
    id: int
    interface_name: str
    device_id: int
    device_name: str
    mode: str


@dataclass(frozen=True)
class VlanUsage:
    vlan: VlanSummary
    subnets: list[VlanSubnet]
    interfaces: list[VlanInterface]


@dataclass(frozen=True)
class SubnetSummary:
    id: int
    cidr: str
    version: int
    prefix_length: int
    site_id: int
    site_name: str
    parent_id: int | None
    parent_cidr: str | None
    depth: int
    utilisation: float
    vlan_id: int | None
    vlan_number: int | None
    vlan_name: str | None


def list_vlans(actor, site_id: int | None = None) -> list[VlanSummary]:
    require_role(actor)
    vlans = Vlan.objects.select_related("site").order_by("site__name", "site_id", "number")
    if site_id is not None:
        vlans = vlans.filter(site_id=site_id)
    return [_vlan_summary(vlan) for vlan in vlans]


def get_vlan(actor, vlan_id: int) -> VlanSummary:
    require_role(actor)
    return _vlan_summary(_find_vlan(vlan_id))


def create_vlan(actor, number: int, site_id: int, name: str = "") -> VlanSummary:
    require_editor(actor)
    with _unique_vlan_number():
        vlan = create_item(actor, Vlan, number=number, site_id=site_id, name=name.strip())
    return _vlan_summary(_find_vlan(vlan.pk))


def update_vlan(actor, vlan_id: int, **values) -> VlanSummary:
    """Renumber or rename a VLAN. Its Site is fixed, as Interfaces and Subnets depend on it."""
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_VLAN_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a VLAN")
    if "name" in values:
        values["name"] = values["name"].strip()
    with _unique_vlan_number():
        update_item(actor, _find_vlan(vlan_id), **values)
    return _vlan_summary(_find_vlan(vlan_id))


def delete_vlan(actor, vlan_id: int) -> None:
    """Delete an unused VLAN. Anything still using it must be changed first."""
    require_editor(actor)
    with transaction.atomic():
        # The lock stops an Interface from gaining this VLAN while it is being deleted.
        vlan = Vlan.objects.select_for_update().filter(pk=vlan_id).first()
        if vlan is None:
            raise Http404
        subnets = vlan.subnets.count()
        interfaces = vlan.untagged_interfaces.count() + vlan.tagged_interfaces.count()
        if subnets or interfaces:
            raise ValidationError(
                _("This VLAN is still used by %(subnets)d Subnets and %(interfaces)d Interfaces.")
                % {"subnets": subnets, "interfaces": interfaces}
            )
        delete_item(actor, vlan)


def vlan_usage(actor, vlan_id: int) -> VlanUsage:
    """The Subnets bound to a VLAN and the Interfaces carrying it."""
    require_role(actor)
    vlan = _find_vlan(vlan_id)
    carried = [
        (interface, mode)
        for manager, mode in (
            (vlan.untagged_interfaces, "untagged"),
            (vlan.tagged_interfaces, "tagged"),
        )
        for interface in manager.select_related("device")
    ]
    carried.sort(key=lambda pair: (pair[0].device.name, pair[0].name, pair[0].pk))
    subnets = sorted(vlan.subnets.all(), key=lambda s: _network_order(ip_network(s.cidr)))
    return VlanUsage(
        vlan=_vlan_summary(vlan),
        subnets=[VlanSubnet(id=subnet.pk, cidr=subnet.cidr) for subnet in subnets],
        interfaces=[
            VlanInterface(
                id=interface.pk,
                interface_name=interface.name,
                device_id=interface.device_id,
                device_name=interface.device.name,
                mode=mode,
            )
            for interface, mode in carried
        ],
    )


def _find_vlan(vlan_id: int):
    vlan = Vlan.objects.select_related("site").filter(pk=vlan_id).first()
    if vlan is None:
        raise Http404
    return vlan


def _vlan_summary(vlan) -> VlanSummary:
    return VlanSummary(
        id=vlan.pk,
        number=vlan.number,
        name=vlan.name,
        site_id=vlan.site_id,
        site_name=vlan.site.name,
    )


@contextmanager
def _unique_vlan_number():
    try:
        yield
    except IntegrityError as error:
        # Two requests raced for the same number after both passed validation.
        message = _("This VLAN number already exists in this Site.")
        raise ValidationError({"number": [message]}) from error


def _network_order(network) -> tuple:
    return network.version, int(network.network_address), network.prefixlen


def _check_vlan(vlan_id: int | None, site_id: int) -> None:
    if vlan_id is None:
        return
    vlan = Vlan.objects.filter(pk=vlan_id).first()
    if vlan is None:
        raise ValidationError({"vlan_id": [_("This VLAN does not exist.")]})
    if vlan.site_id != site_id:
        raise ValidationError({"vlan_id": [_("Choose a VLAN in the Subnet's Site.")]})


def list_subnets(
    actor, site_id: int | None = None, version: int | None = None
) -> list[SubnetSummary]:
    """Subnets in tree order: each one is directly followed by those nested in it."""
    require_role(actor)
    subnets = Subnet.objects.select_related("site", "vlan")
    if site_id is not None:
        subnets = subnets.filter(site_id=site_id)
    summaries = _tree(subnets)
    if version is not None:
        summaries = [summary for summary in summaries if summary.version == version]
    return summaries


def get_subnet(actor, subnet_id: int) -> SubnetSummary:
    require_role(actor)
    return _summary_of(_find(subnet_id))


def create_subnet(
    actor, cidr: str, site_id: int, vlan_id: int | None = None
) -> SubnetSummary:
    require_editor(actor)
    network = _parse(cidr)
    _check_vlan(vlan_id, site_id)
    with _unique_in_site():
        subnet = create_item(
            actor, Subnet, cidr=str(network), site_id=site_id, vlan_id=vlan_id
        )
    return _summary_of(subnet)


def update_subnet(actor, subnet_id: int, **values) -> SubnetSummary:
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a Subnet")
    subnet = _find(subnet_id)
    if "cidr" in values:
        values["cidr"] = str(_parse(values["cidr"]))
    site_id = values.get("site_id", subnet.site_id)
    if "vlan_id" in values:
        _check_vlan(values["vlan_id"], site_id)
    elif subnet.vlan_id and site_id != subnet.site_id:
        message = _("Remove this Subnet's VLAN before moving it to another Site.")
        raise ValidationError({"site_id": [message]})
    with _unique_in_site():
        updated = update_item(actor, subnet, **values)
    return _summary_of(_find(updated.pk))


def delete_subnet(actor, subnet_id: int) -> None:
    """Delete one Subnet. Those nested in it stay and move up the tree."""
    require_editor(actor)
    delete_item(actor, _find(subnet_id))


def _parse(cidr):
    """The network for CIDR text. Host bits are refused, as they are usually a typo."""
    text = cidr.strip() if isinstance(cidr, str) else ""
    _address, slash, prefix = text.partition("/")
    if not slash or "%" in text or not (prefix.isascii() and prefix.isdecimal()):
        raise _invalid(_("Write a Subnet in CIDR notation, such as 192.168.1.0/24."))
    try:
        return ip_network(text, strict=True)
    except ValueError:
        pass
    try:
        suggestion = ip_network(text, strict=False)
    except ValueError as error:
        message = _("Write a Subnet in CIDR notation, such as 192.168.1.0/24.")
        raise _invalid(message) from error
    raise _invalid(_("This address has host bits set. Did you mean %(cidr)s?") % {
        "cidr": suggestion
    })


def _invalid(message: str) -> ValidationError:
    return ValidationError({"cidr": [message]})


@contextmanager
def _unique_in_site():
    try:
        yield
    except IntegrityError as error:
        # Two requests raced for the same Subnet after both passed validation.
        raise _invalid(_("This Subnet already exists in this Site.")) from error


def _find(subnet_id: int):
    subnet = Subnet.objects.select_related("site", "vlan").filter(pk=subnet_id).first()
    if subnet is None:
        raise Http404
    return subnet


def _summary_of(subnet) -> SubnetSummary:
    siblings = Subnet.objects.select_related("site", "vlan").filter(site_id=subnet.site_id)
    return next(summary for summary in _tree(siblings) if summary.id == subnet.pk)


def _tree(subnets) -> list[SubnetSummary]:
    """Walk the Subnets in address order, keeping the chain of those containing the current one."""
    entries = sorted(
        ((subnet, ip_network(subnet.cidr)) for subnet in subnets),
        key=lambda entry: (
            entry[0].site.name,
            entry[0].site_id,
            entry[1].version,
            int(entry[1].network_address),
            entry[1].prefixlen,
        ),
    )

    placed = []
    covered = defaultdict(int)
    chain = []
    for subnet, network in entries:
        while chain and not _contains(chain[-1], subnet, network):
            chain.pop()
        parent = chain[-1][0] if chain else None
        if parent is not None:
            # Only direct children count, so nested space is never counted twice.
            covered[parent.pk] += network.num_addresses
        placed.append((subnet, network, parent, len(chain)))
        chain.append((subnet, network))

    return [
        SubnetSummary(
            id=subnet.pk,
            cidr=subnet.cidr,
            version=network.version,
            prefix_length=network.prefixlen,
            site_id=subnet.site_id,
            site_name=subnet.site.name,
            parent_id=parent.pk if parent else None,
            parent_cidr=parent.cidr if parent else None,
            depth=depth,
            # Rounded down, so a Subnet only ever shows 100 % when it is really full.
            utilisation=covered[subnet.pk] * 1000 // network.num_addresses / 10,
            vlan_id=subnet.vlan_id,
            vlan_number=subnet.vlan.number if subnet.vlan_id else None,
            vlan_name=subnet.vlan.name if subnet.vlan_id else None,
        )
        for subnet, network, parent, depth in placed
    ]


def _contains(outer, subnet, network) -> bool:
    outer_subnet, outer_network = outer
    return (
        outer_subnet.site_id == subnet.site_id
        and outer_network.version == network.version
        and network.subnet_of(outer_network)
    )
