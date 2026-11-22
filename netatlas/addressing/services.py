"""VLANs, Subnets, IP Addresses and DHCP Ranges.

The Subnet tree and each address's Subnet are derived from the addresses, so they cannot go stale.
"""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.addressing.models import DhcpRange, IpAddress, Subnet, Vlan
from netatlas.devices.models import Interface
from netatlas.history.services import create_item, delete_item, update_item
from netatlas.system.locks import lock_inventory

_UPDATABLE_FIELDS = {"cidr", "site_id", "vlan_id"}
_UPDATABLE_VLAN_FIELDS = {"number", "name"}
_UPDATABLE_ADDRESS_FIELDS = {"address", "description", "interface_id"}
_UPDATABLE_RANGE_FIELDS = {"start", "end", "description"}


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


@dataclass(frozen=True)
class IpAddressSummary:
    id: int
    address: str
    version: int
    status: str
    site_id: int
    site_name: str
    subnet_id: int | None
    subnet_cidr: str | None
    interface_id: int | None
    interface_name: str | None
    device_id: int | None
    device_name: str | None
    description: str


@dataclass(frozen=True)
class DhcpRangeSummary:
    id: int
    subnet_id: int
    subnet_cidr: str
    start: str
    end: str
    # Text, because the size of an IPv6 range does not fit a JSON number.
    size: str
    description: str


@dataclass(frozen=True)
class SubnetUsage:
    subnet: SubnetSummary
    assigned: int
    reserved: int
    # Text, because IPv6 counts do not fit a JSON number.
    dhcp: str
    nested: str
    free: str
    next_free: str | None
    ip_addresses: list[IpAddressSummary]
    dhcp_ranges: list[DhcpRangeSummary]


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
    with transaction.atomic():
        lock_inventory()
        with _unique_in_site():
            subnet = create_item(
                actor, Subnet, cidr=str(network), site_id=site_id, vlan_id=vlan_id
            )
        _check_site_plan(site_id)
    return _summary_of(subnet)


def update_subnet(actor, subnet_id: int, **values) -> SubnetSummary:
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a Subnet")
    if "cidr" in values:
        values["cidr"] = str(_parse(values["cidr"]))
    with transaction.atomic():
        lock_inventory()
        subnet = _find(subnet_id)
        site_id = values.get("site_id", subnet.site_id)
        if "vlan_id" in values:
            _check_vlan(values["vlan_id"], site_id)
        elif subnet.vlan_id and site_id != subnet.site_id:
            message = _("Remove this Subnet's VLAN before moving it to another Site.")
            raise ValidationError({"site_id": [message]})
        if site_id != subnet.site_id and _holds_addresses(subnet):
            message = _("Remove this Subnet's IP Addresses and DHCP Ranges before moving it.")
            raise ValidationError({"site_id": [message]})
        with _unique_in_site():
            updated = update_item(actor, subnet, **values)
        # A failed check rolls the change back, so the plan is never left inconsistent.
        for affected in {subnet.site_id, updated.site_id}:
            _check_site_plan(affected)
    return _summary_of(_find(updated.pk))


def delete_subnet(actor, subnet_id: int) -> None:
    """Delete one Subnet. What is nested in it moves up, but nothing may be left outside."""
    require_editor(actor)
    with transaction.atomic():
        lock_inventory()
        subnet = _find(subnet_id)
        if subnet.dhcp_ranges.exists():
            message = _("Delete this Subnet's DHCP Ranges first.")
            raise ValidationError({"cidr": [message]})
        site_id = subnet.site_id
        delete_item(actor, subnet)
        _check_site_plan(site_id)


def subnet_usage(actor, subnet_id: int) -> SubnetUsage:
    """How a Subnet's own space is used, not counting inside nested Subnets."""
    require_role(actor)
    subnet = _find(subnet_id)
    summary = _summary_of(subnet)
    network = ip_network(subnet.cidr)
    entries = _site_entries([subnet.site_id])[subnet.site_id]
    children = [
        ip_network(entry.cidr)
        for entry in _tree(Subnet.objects.select_related("site", "vlan").filter(
            site_id=subnet.site_id
        ))
        if entry.parent_id == subnet.pk
    ]
    items = list(IpAddress.objects.filter(site_id=subnet.site_id))
    located = _locate(entries, [ip_address(item.address) for item in items])
    own = [
        item
        for item in items
        if (entry := located[ip_address(item.address)]) and entry[0].pk == subnet.pk
    ]
    ranges = sorted(subnet.dhcp_ranges.all(), key=lambda r: int(ip_address(r.start)))
    dhcp = sum(_range_size(item) for item in ranges)
    free, _utilisation = _occupancy(network, children, len(own), dhcp)

    blocked = [(int(c.network_address), int(c.broadcast_address)) for c in children]
    blocked += [(int(ip_address(i.address)), int(ip_address(i.address))) for i in own]
    blocked += [(int(ip_address(r.start)), int(ip_address(r.end))) for r in ranges]
    return SubnetUsage(
        subnet=summary,
        assigned=sum(1 for item in own if item.interface_id),
        reserved=sum(1 for item in own if not item.interface_id),
        dhcp=str(dhcp),
        nested=str(sum(child.num_addresses for child in children)),
        free=str(free),
        next_free=_next_free(network, blocked),
        ip_addresses=_address_summaries(
            IpAddress.objects.select_related("site", "interface__device").filter(
                pk__in=[item.pk for item in own]
            )
        ),
        dhcp_ranges=[_range_summary(item) for item in ranges],
    )


def list_ip_addresses(
    actor,
    site_id: int | None = None,
    subnet_id: int | None = None,
    interface_id: int | None = None,
    status: str | None = None,
) -> list[IpAddressSummary]:
    """IP Addresses in numeric order."""
    require_role(actor)
    items = IpAddress.objects.select_related("site", "interface__device")
    if site_id is not None:
        items = items.filter(site_id=site_id)
    if interface_id is not None:
        items = items.filter(interface_id=interface_id)
    if status == "assigned":
        items = items.filter(interface__isnull=False)
    elif status == "reserved":
        items = items.filter(interface__isnull=True)
    summaries = _address_summaries(items)
    if subnet_id is not None:
        summaries = [summary for summary in summaries if summary.subnet_id == subnet_id]
    return summaries


def get_ip_address(actor, address_id: int) -> IpAddressSummary:
    require_role(actor)
    return _address_summaries([_find_address(address_id)])[0]


def create_ip_address(
    actor,
    address: str,
    site_id: int,
    interface_id: int | None = None,
    description: str = "",
) -> IpAddressSummary:
    """Document an address. It is Assigned with an Interface and Reserved without one."""
    require_editor(actor)
    parsed = _parse_address(address)
    with transaction.atomic():
        lock_inventory()
        _check_address(parsed, site_id)
        _check_interface(interface_id, site_id)
        item = create_item(
            actor,
            IpAddress,
            address=str(parsed),
            site_id=site_id,
            interface_id=interface_id,
            description=description.strip(),
        )
    return get_ip_address(actor, item.pk)


def reserve_ip_address(
    actor, subnet_id: int, address: str, description: str = ""
) -> IpAddressSummary:
    """Document a Reserved address, which must lie inside the given Subnet."""
    require_editor(actor)
    subnet = _find(subnet_id)
    if _parse_address(address) not in ip_network(subnet.cidr):
        message = _("This address is not inside %(cidr)s.") % {"cidr": subnet.cidr}
        raise ValidationError({"address": [message]})
    return create_ip_address(
        actor, address=address, site_id=subnet.site_id, description=description
    )


def update_ip_address(actor, address_id: int, **values) -> IpAddressSummary:
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_ADDRESS_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on an IP Address")
    with transaction.atomic():
        lock_inventory()
        item = _find_address(address_id)
        if "address" in values:
            parsed = _parse_address(values["address"])
            _check_address(parsed, item.site_id)
            values["address"] = str(parsed)
        if "interface_id" in values:
            _check_interface(values["interface_id"], item.site_id)
        if "description" in values:
            values["description"] = values["description"].strip()
        update_item(actor, item, **values)
    return get_ip_address(actor, address_id)


def delete_ip_address(actor, address_id: int) -> None:
    require_editor(actor)
    with transaction.atomic():
        lock_inventory()
        delete_item(actor, _find_address(address_id))


def assign_ip_address(actor, interface_id: int, address: str) -> IpAddressSummary:
    """Put an address on an Interface, using its Reserved entry or documenting it now."""
    require_editor(actor)
    parsed = _parse_address(address)
    with transaction.atomic():
        lock_inventory()
        interface = Interface.objects.select_related("device").filter(pk=interface_id).first()
        if interface is None:
            raise Http404
        site_id = interface.device.site_id
        item = IpAddress.objects.filter(site_id=site_id, address=str(parsed)).first()
        if item is None:
            _check_address(parsed, site_id)
            item = create_item(
                actor, IpAddress, address=str(parsed), site_id=site_id, interface_id=interface_id
            )
        elif item.interface_id not in (None, interface_id):
            message = _("This IP Address is already Assigned to another Interface.")
            raise ValidationError({"address": [message]})
        else:
            update_item(actor, item, interface_id=interface_id)
    return get_ip_address(actor, item.pk)


def unassign_ip_address(actor, address_id: int) -> IpAddressSummary:
    """Take an address off its Interface. It stays documented as Reserved."""
    return update_ip_address(actor, address_id, interface_id=None)


def list_dhcp_ranges(actor, subnet_id: int | None = None) -> list[DhcpRangeSummary]:
    require_role(actor)
    ranges = DhcpRange.objects.select_related("subnet")
    if subnet_id is not None:
        ranges = ranges.filter(subnet_id=subnet_id)
    ordered = sorted(ranges, key=lambda r: _address_order(ip_address(r.start)))
    return [_range_summary(item) for item in ordered]


def get_dhcp_range(actor, range_id: int) -> DhcpRangeSummary:
    require_role(actor)
    return _range_summary(_find_range(range_id))


def create_dhcp_range(
    actor, subnet_id: int, start: str, end: str, description: str = ""
) -> DhcpRangeSummary:
    require_editor(actor)
    first = _parse_bound(start, "start")
    last = _parse_bound(end, "end")
    with transaction.atomic():
        lock_inventory()
        subnet = Subnet.objects.filter(pk=subnet_id).first()
        if subnet is None:
            raise ValidationError({"subnet_id": [_("This Subnet does not exist.")]})
        _check_range(subnet, first, last)
        item = create_item(
            actor,
            DhcpRange,
            subnet=subnet,
            start=str(first),
            end=str(last),
            description=description.strip(),
        )
    return get_dhcp_range(actor, item.pk)


def update_dhcp_range(actor, range_id: int, **values) -> DhcpRangeSummary:
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_RANGE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a DHCP Range")
    with transaction.atomic():
        lock_inventory()
        item = _find_range(range_id)
        first = _parse_bound(values.get("start", item.start), "start")
        last = _parse_bound(values.get("end", item.end), "end")
        _check_range(item.subnet, first, last, exclude_id=item.pk)
        if "start" in values:
            values["start"] = str(first)
        if "end" in values:
            values["end"] = str(last)
        if "description" in values:
            values["description"] = values["description"].strip()
        update_item(actor, item, **values)
    return get_dhcp_range(actor, range_id)


def delete_dhcp_range(actor, range_id: int) -> None:
    require_editor(actor)
    with transaction.atomic():
        lock_inventory()
        delete_item(actor, _find_range(range_id))


def _find_address(address_id: int):
    item = (
        IpAddress.objects.select_related("site", "interface__device")
        .filter(pk=address_id)
        .first()
    )
    if item is None:
        raise Http404
    return item


def _find_range(range_id: int):
    item = DhcpRange.objects.select_related("subnet").filter(pk=range_id).first()
    if item is None:
        raise Http404
    return item


def _parse_address(text, field: str = "address"):
    value = text.strip() if isinstance(text, str) else ""
    if "/" in value or "%" in value:
        raise ValidationError({field: [_("Write a single IP Address, without a prefix.")]})
    try:
        return ip_address(value)
    except ValueError as error:
        raise ValidationError({field: [_("Enter a valid IP Address.")]}) from error


def _parse_bound(text, field: str):
    return _parse_address(text, field)


def _address_order(address) -> tuple[int, int]:
    return address.version, int(address)


def _special_addresses(network) -> list:
    """The network and broadcast address of an IPv4 Subnet. /31, /32 and IPv6 have none."""
    if network.version == 4 and network.prefixlen < 31:
        return [network.network_address, network.broadcast_address]
    return []


def _range_size(item) -> int:
    return int(ip_address(item.end)) - int(ip_address(item.start)) + 1


def _site_entries(site_ids) -> dict:
    """Each Site's (Subnet, network) pairs, ordered so a parent precedes what it contains."""
    entries = defaultdict(list)
    subnets = Subnet.objects.filter(site_id__in=site_ids)
    for subnet in subnets:
        entries[subnet.site_id].append((subnet, ip_network(subnet.cidr)))
    for site_entries in entries.values():
        site_entries.sort(key=lambda entry: _network_order(entry[1]))
    return entries


def _locate(entries, addresses) -> dict:
    """The most specific (Subnet, network) of each address, or None. One sweep, in order."""
    located = {}
    index = 0
    chain = []
    for address in sorted(set(addresses), key=_address_order):
        order = _address_order(address)
        while index < len(entries):
            entry = entries[index]
            if (entry[1].version, int(entry[1].network_address)) > order:
                break
            while chain and not (
                chain[-1][1].version == entry[1].version and entry[1].subnet_of(chain[-1][1])
            ):
                chain.pop()
            chain.append(entry)
            index += 1
        while chain and address not in chain[-1][1]:
            chain.pop()
        located[address] = chain[-1] if chain else None
    return located


def _occupancy(network, children, own: int, dhcp: int) -> tuple[int, float]:
    """A Subnet's free addresses and its utilisation in percent."""
    uncovered = sum(
        1
        for special in _special_addresses(network)
        if not any(special in child for child in children)
    )
    covered = sum(child.num_addresses for child in children)
    total = network.num_addresses
    free = total - covered - own - dhcp - uncovered
    if free == 0:
        return 0, 100.0
    capacity = total if children else total - uncovered
    # Rounded down, so a Subnet only ever shows 100 % when it is really full.
    return free, min((covered + own + dhcp) * 1000 // capacity / 10, 99.9)


def _next_free(network, blocked) -> str | None:
    """The first usable address outside the blocked (start, end) intervals."""
    special = _special_addresses(network)
    candidate = int(network.network_address) + (1 if special else 0)
    last = int(network.broadcast_address) - (1 if special else 0)
    for start, end in sorted(blocked):
        if candidate < start:
            break
        candidate = max(candidate, end + 1)
    if candidate > last:
        return None
    return str(type(network.network_address)(candidate))


def _holds_addresses(subnet) -> bool:
    if subnet.dhcp_ranges.exists():
        return True
    network = ip_network(subnet.cidr)
    addresses = IpAddress.objects.filter(site_id=subnet.site_id).values_list("address", flat=True)
    return any(ip_address(address) in network for address in addresses)


def _check_address(address, site_id: int) -> None:
    entries = _site_entries([site_id]).get(site_id, [])
    entry = _locate(entries, [address])[address]
    if entry is None:
        message = _("This address is not inside any Subnet of this Site.")
        raise ValidationError({"address": [message]})
    if address in _special_addresses(entry[1]):
        message = _("This is the network or broadcast address of %(cidr)s.") % {
            "cidr": entry[0].cidr
        }
        raise ValidationError({"address": [message]})
    for item in DhcpRange.objects.filter(subnet__site_id=site_id):
        start, end = ip_address(item.start), ip_address(item.end)
        if start.version == address.version and start <= address <= end:
            message = _("This address is inside the DHCP Range %(range)s.") % {"range": item}
            raise ValidationError({"address": [message]})


def _check_interface(interface_id: int | None, site_id: int) -> None:
    if interface_id is None:
        return
    interface = Interface.objects.select_related("device").filter(pk=interface_id).first()
    if interface is None:
        raise ValidationError({"interface_id": [_("This Interface does not exist.")]})
    if interface.device.site_id != site_id:
        message = _("Choose an Interface in the IP Address's Site.")
        raise ValidationError({"interface_id": [message]})


def _check_range(subnet, first, last, exclude_id: int | None = None) -> None:
    network = ip_network(subnet.cidr)
    if first.version != network.version or last.version != network.version:
        raise ValidationError({"start": [_("Use addresses of the Subnet's IP version.")]})
    if first > last:
        raise ValidationError({"end": [_("The end must not be before the start.")]})
    if first not in network or last not in network:
        raise ValidationError({"start": [_("The range must be inside the Subnet.")]})
    special = _special_addresses(network)
    if first in special or last in special:
        message = _("The range cannot include the network or broadcast address.")
        raise ValidationError({"start": [message]})

    others = DhcpRange.objects.filter(subnet__site_id=subnet.site_id)
    if exclude_id is not None:
        others = others.exclude(pk=exclude_id)
    for other in others:
        start, end = ip_address(other.start), ip_address(other.end)
        if start.version == first.version and start <= last and first <= end:
            message = _("This overlaps the DHCP Range %(range)s.") % {"range": other}
            raise ValidationError({"start": [message]})

    for entry in _site_entries([subnet.site_id]).get(subnet.site_id, []):
        inner = entry[1]
        if (
            entry[0].pk != subnet.pk
            and inner.version == network.version
            and inner.subnet_of(network)
            and inner.network_address <= last
            and first <= inner.broadcast_address
        ):
            message = _("This overlaps the nested Subnet %(cidr)s.") % {"cidr": entry[0].cidr}
            raise ValidationError({"start": [message]})

    for text in IpAddress.objects.filter(site_id=subnet.site_id).values_list("address", flat=True):
        address = ip_address(text)
        if address.version == first.version and first <= address <= last:
            message = _("This covers the IP Address %(address)s.") % {"address": text}
            raise ValidationError({"start": [message]})


def _check_site_plan(site_id: int) -> None:
    """Every address stays in a Subnet and usable, and every DHCP Range stays valid."""
    entries = _site_entries([site_id]).get(site_id, [])
    texts = IpAddress.objects.filter(site_id=site_id).values_list("address", flat=True)
    located = _locate(entries, [ip_address(text) for text in texts])

    outside = sum(1 for entry in located.values() if entry is None)
    if outside:
        message = _("%(count)d IP Addresses would no longer be inside any Subnet.") % {
            "count": outside
        }
        raise _invalid(message)
    for address, entry in located.items():
        if address in _special_addresses(entry[1]):
            message = _("%(address)s would become the network or broadcast address of %(cidr)s.")
            raise _invalid(message % {"address": address, "cidr": entry[0].cidr})

    for item in DhcpRange.objects.select_related("subnet").filter(subnet__site_id=site_id):
        network = ip_network(item.subnet.cidr)
        first, last = ip_address(item.start), ip_address(item.end)
        special = _special_addresses(network)
        if (
            first.version != network.version
            or first not in network
            or last not in network
            or first in special
            or last in special
        ):
            raise _invalid(_("The DHCP Range %(range)s would no longer fit.") % {"range": item})
        for subnet, inner in entries:
            if (
                subnet.pk != item.subnet_id
                and inner.version == network.version
                and inner.subnet_of(network)
                and inner.network_address <= last
                and first <= inner.broadcast_address
            ):
                message = _("This would overlap the DHCP Range %(range)s.") % {"range": item}
                raise _invalid(message)


def _address_summaries(items) -> list[IpAddressSummary]:
    items = list(items)
    entries = _site_entries({item.site_id for item in items})
    located = {
        site_id: _locate(
            entries.get(site_id, []),
            [ip_address(item.address) for item in items if item.site_id == site_id],
        )
        for site_id in {item.site_id for item in items}
    }
    ordered = sorted(items, key=lambda item: _address_order(ip_address(item.address)))
    return [_address_summary(item, located[item.site_id]) for item in ordered]


def _address_summary(item, located) -> IpAddressSummary:
    address = ip_address(item.address)
    entry = located[address]
    interface = item.interface
    return IpAddressSummary(
        id=item.pk,
        address=item.address,
        version=address.version,
        status="assigned" if interface else "reserved",
        site_id=item.site_id,
        site_name=item.site.name,
        subnet_id=entry[0].pk if entry else None,
        subnet_cidr=entry[0].cidr if entry else None,
        interface_id=item.interface_id,
        interface_name=interface.name if interface else None,
        device_id=interface.device_id if interface else None,
        device_name=interface.device.name if interface else None,
        description=item.description,
    )


def _range_summary(item) -> DhcpRangeSummary:
    return DhcpRangeSummary(
        id=item.pk,
        subnet_id=item.subnet_id,
        subnet_cidr=item.subnet.cidr,
        start=item.start,
        end=item.end,
        size=str(_range_size(item)),
        description=item.description,
    )


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
    # Only direct children count, so nested space is never counted twice.
    children = defaultdict(list)
    chain = []
    by_site = defaultdict(list)
    for subnet, network in entries:
        while chain and not _contains(chain[-1], subnet, network):
            chain.pop()
        parent = chain[-1][0] if chain else None
        if parent is not None:
            children[parent.pk].append(network)
        placed.append((subnet, network, parent, len(chain)))
        chain.append((subnet, network))
        by_site[subnet.site_id].append((subnet, network))

    own = defaultdict(int)
    texts = defaultdict(list)
    for site_id, text in IpAddress.objects.filter(site_id__in=by_site).values_list(
        "site_id", "address"
    ):
        texts[site_id].append(ip_address(text))
    for site_id, addresses in texts.items():
        for entry in _locate(by_site[site_id], addresses).values():
            if entry is not None:
                own[entry[0].pk] += 1
    dhcp = defaultdict(int)
    for item in DhcpRange.objects.filter(subnet__site_id__in=by_site):
        dhcp[item.subnet_id] += _range_size(item)

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
            utilisation=_occupancy(
                network, children[subnet.pk], own[subnet.pk], dhcp[subnet.pk]
            )[1],
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
