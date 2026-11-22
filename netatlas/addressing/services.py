"""Subnets. Their tree is derived from the addresses, so it can never go stale."""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import ip_network

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.addressing.models import Subnet
from netatlas.history.services import create_item, delete_item, update_item

_UPDATABLE_FIELDS = {"cidr", "site_id"}


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


def list_subnets(
    actor, site_id: int | None = None, version: int | None = None
) -> list[SubnetSummary]:
    """Subnets in tree order: each one is directly followed by those nested in it."""
    require_role(actor)
    subnets = Subnet.objects.select_related("site")
    if site_id is not None:
        subnets = subnets.filter(site_id=site_id)
    summaries = _tree(subnets)
    if version is not None:
        summaries = [summary for summary in summaries if summary.version == version]
    return summaries


def get_subnet(actor, subnet_id: int) -> SubnetSummary:
    require_role(actor)
    return _summary_of(_find(subnet_id))


def create_subnet(actor, cidr: str, site_id: int) -> SubnetSummary:
    require_editor(actor)
    network = _parse(cidr)
    with _unique_in_site():
        subnet = create_item(actor, Subnet, cidr=str(network), site_id=site_id)
    return _summary_of(subnet)


def update_subnet(actor, subnet_id: int, **values) -> SubnetSummary:
    require_editor(actor)
    unknown = set(values) - _UPDATABLE_FIELDS
    if unknown:
        raise TypeError(f"Cannot update {', '.join(sorted(unknown))} on a Subnet")
    subnet = _find(subnet_id)
    if "cidr" in values:
        values["cidr"] = str(_parse(values["cidr"]))
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
    subnet = Subnet.objects.select_related("site").filter(pk=subnet_id).first()
    if subnet is None:
        raise Http404
    return subnet


def _summary_of(subnet) -> SubnetSummary:
    siblings = Subnet.objects.select_related("site").filter(site_id=subnet.site_id)
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
