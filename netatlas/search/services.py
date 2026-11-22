"""Global search across documented items and Page text, in Postgres alone.

Each kind of text is matched the way it is written:
- Page text uses full-text search, with no stemming because Pages can be in any language.
- Names match as substrings, because the full-text parser splits names like `ge-0/0/1`.
- An IP Address or CIDR is compared as an address, so `10.0.0.5` finds the Subnets containing it
  and never `10.0.0.50`.
"""

import ipaddress
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.exceptions import ValidationError
from django.db.models import Case, F, Func, IntegerField, Q, TextField, Value, When
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_role
from netatlas.addressing.models import IpAddress, Subnet, Vlan
from netatlas.devices.models import Device, Interface, Rack
from netatlas.pages.models import ITEM_PAGE_MODELS, Page
from netatlas.places.models import Customer, Site

MAX_QUERY_LENGTH = 200
MAX_HITS_PER_KIND = 20
EXCERPT_RADIUS = 70

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Stored references hold kinds and IDs. Nobody means those when searching a Page's text.
_REFERENCES = r"@\[[a-z-]+:[0-9]+\]|@\{[a-z-]+:[0-9]+\.[a-z-]+\}|\(attachment:[0-9]+\)"


@dataclass(frozen=True)
class SearchHit:
    kind: str
    id: int
    label: str
    # Where the item is, such as its Site.
    detail: str
    # The matching part of a Page's text.
    excerpt: str


@dataclass(frozen=True)
class SearchGroup:
    kind: str
    # Every match. `hits` holds the best of them.
    count: int
    hits: list[SearchHit]


@dataclass(frozen=True)
class SearchResults:
    query: str
    total: int
    groups: list[SearchGroup]


@dataclass(frozen=True)
class _Kind:
    model: type
    fields: tuple[str, ...]
    order: str
    site_path: str
    related: tuple[str, ...]
    detail: Callable


def _ip_detail(address) -> str:
    parts = [address.site.name]
    if address.interface:
        parts.append(str(address.interface))
    return " · ".join(parts)


_KINDS = {
    "device": _Kind(Device, ("name",), "name", "site", ("site",), lambda d: d.site.name),
    "interface": _Kind(
        Interface,
        ("name", "device__name"),
        "name",
        "device__site",
        ("device__site",),
        lambda i: i.device.site.name,
    ),
    "ip-address": _Kind(
        IpAddress,
        ("address", "description"),
        "address",
        "site",
        ("site", "interface__device"),
        _ip_detail,
    ),
    "subnet": _Kind(Subnet, ("cidr",), "cidr", "site", ("site",), lambda s: s.site.name),
    "vlan": _Kind(Vlan, ("name",), "number", "site", ("site",), lambda v: v.site.name),
    "site": _Kind(
        Site,
        ("name",),
        "name",
        "pk",
        ("customer",),
        lambda s: s.customer.name if s.customer else "",
    ),
    "rack": _Kind(Rack, ("name",), "name", "site", ("site",), lambda r: r.site.name),
    "customer": _Kind(Customer, ("name",), "name", "sites", (), lambda _customer: ""),
}
SEARCH_KINDS = (*_KINDS, "page")


def search(actor, query: str = "", tags: Iterable[str] = (), site_id=None) -> SearchResults:
    """Everything matching every word of the query, and carrying every Tag, grouped by kind."""
    require_role(actor)
    clean = _clean_query(query)
    keys = _tag_keys(tags)
    _require_site(site_id)
    if not clean and not keys and site_id is None:
        return SearchResults(query=clean, total=0, groups=[])

    address = _parse_address(clean)
    groups = [_search_kind(kind, clean, address, keys, site_id) for kind in _KINDS]
    groups.append(_search_pages(clean, address, keys, site_id))
    found = [group for group in groups if group.count]
    return SearchResults(query=clean, total=sum(g.count for g in found), groups=found)


def _clean_query(query) -> str:
    text = _CONTROL.sub(" ", query if isinstance(query, str) else "").strip()
    if len(text) > MAX_QUERY_LENGTH:
        message = _("A search has at most %(limit)d characters.") % {"limit": MAX_QUERY_LENGTH}
        raise ValidationError({"q": [message]})
    return " ".join(text.split())


def _tag_keys(tags) -> list[str]:
    names = list(tags)
    if not all(isinstance(name, str) and name.strip() for name in names):
        raise ValidationError({"tag": [_("Enter a Tag name.")]})
    return sorted({name.strip().casefold() for name in names})


def _require_site(site_id) -> None:
    if site_id is not None and not Site.objects.filter(pk=site_id).exists():
        raise ValidationError({"site": [_("Choose an existing Site.")]})


def _parse_address(query: str):
    """The query as one address or network, if that is what it is."""
    if not query or " " in query:
        return None
    try:
        return ipaddress.ip_address(query)
    except ValueError:
        pass
    if "/" not in query:
        return None
    try:
        return ipaddress.ip_network(query, strict=False)
    except ValueError:
        return None


def _with_tags(items, keys):
    for key in keys:
        items = items.filter(tags__key=key)
    return items


def _search_kind(kind, query, address, keys, site_id) -> SearchGroup:
    spec = _KINDS[kind]
    items = _with_tags(spec.model.objects.all(), keys)
    if site_id is not None:
        items = items.filter(**{spec.site_path: site_id})

    if address is not None and kind == "ip-address":
        return _group_of_ids(kind, spec, _matching_addresses(items, address))
    if address is not None and kind == "subnet":
        return _group_of_ids(kind, spec, _matching_subnets(items, address))

    words = query.split()
    for word in words:
        items = items.filter(_word_matches(kind, spec, word))
    if query:
        items = items.annotate(
            exact=Case(
                When(_exact_match(kind, spec, query), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by("-exact", spec.order, "pk")
    else:
        items = items.order_by(spec.order, "pk")
    count = items.count()
    hits = items.select_related(*spec.related)[:MAX_HITS_PER_KIND] if spec.related else items[
        :MAX_HITS_PER_KIND
    ]
    return SearchGroup(kind, count, [_hit(kind, spec, item) for item in hits])


def _word_matches(kind, spec, word) -> Q:
    matches = Q()
    for field in spec.fields:
        matches |= Q(**{f"{field}__icontains": word})
    if kind == "vlan":
        if word.casefold() == "vlan":
            return Q(pk__isnull=False)
        if word.isascii() and word.isdecimal() and int(word) <= 4094:
            matches |= Q(number=int(word))
    return matches


def _exact_match(kind, spec, query) -> Q:
    exact = Q(**{f"{spec.fields[0]}__iexact": query})
    if kind == "vlan":
        number = query.casefold().removeprefix("vlan").strip()
        if number.isascii() and number.isdecimal() and int(number) <= 4094:
            exact |= Q(number=int(number))
    return exact


def _matching_addresses(items, target) -> list[int]:
    rows = items.values_list("pk", "address")
    if not isinstance(target, ipaddress.IPv4Network | ipaddress.IPv6Network):
        return [pk for pk, text in rows if ipaddress.ip_address(text) == target]
    inside = [(ipaddress.ip_address(text), pk) for pk, text in rows]
    found = [(int(ip), pk) for ip, pk in inside if ip.version == target.version and ip in target]
    return [pk for _number, pk in sorted(found)]


def _matching_subnets(items, target) -> list[int]:
    """Subnets containing the address or overlapping the network, most specific first."""
    found = []
    for pk, text in items.values_list("pk", "cidr"):
        network = ipaddress.ip_network(text)
        if network.version != target.version:
            continue
        if isinstance(target, ipaddress.IPv4Network | ipaddress.IPv6Network):
            matches = network.subnet_of(target) or network.supernet_of(target)
            is_exact = network == target
        else:
            matches = target in network
            is_exact = False
        if matches:
            found.append((not is_exact, -network.prefixlen, int(network.network_address), pk))
    return [row[-1] for row in sorted(found)]


def _group_of_ids(kind, spec, ids: list[int]) -> SearchGroup:
    shown = ids[:MAX_HITS_PER_KIND]
    items = spec.model.objects.select_related(*spec.related).in_bulk(shown)
    return SearchGroup(kind, len(ids), [_hit(kind, spec, items[pk]) for pk in shown])


def _hit(kind, spec, item) -> SearchHit:
    return SearchHit(kind, item.pk, str(item), spec.detail(item), "")


def _search_pages(query, address, keys, site_id) -> SearchGroup:
    pages = _with_tags(Page.objects.all(), keys)
    if site_id is not None:
        pages = pages.filter(_pages_in_site(site_id))

    words = [query] if address is not None else query.split()
    if address is not None:
        # Whole addresses only: the parser would read 10.0.0.5 as a prefix of 10.0.0.50.
        pattern = rf"(^|[^0-9a-f:.]){re.escape(query)}($|[^0-9a-f:/]|\.($|[^0-9]))"
        pages = pages.filter(Q(title__iregex=pattern) | Q(body__iregex=pattern)).order_by("pk")
    elif words:
        text = Func(
            F("body"),
            Value(_REFERENCES),
            Value(" "),
            Value("g"),
            function="regexp_replace",
            output_field=TextField(),
        )
        vector = SearchVector("title", weight="A", config="simple") + SearchVector(
            text, weight="B", config="simple"
        )
        terms = SearchQuery(_prefix_query(words), search_type="raw", config="simple")
        pages = (
            pages.annotate(search=vector, rank=SearchRank(vector, terms))
            .filter(search=terms)
            .order_by("-rank", "pk")
        )
    else:
        pages = pages.order_by("pk")

    count = pages.count()
    hits = [
        SearchHit("page", page.pk, str(page), "", _excerpt(page.body, words))
        for page in pages[:MAX_HITS_PER_KIND]
    ]
    return SearchGroup("page", count, hits)


def _prefix_query(words: list[str]) -> str:
    """Every word as a quoted prefix, so nothing typed can act as tsquery syntax."""
    quoted = (word.replace("\\", "\\\\").replace("'", "''") for word in words)
    return " & ".join(f"'{word}':*" for word in quoted)


def _pages_in_site(site_id) -> Q:
    matching = Q()
    for kind, model in ITEM_PAGE_MODELS.items():
        items = model.objects.filter(**{_KINDS[kind].site_path: site_id}).values("pk")
        matching |= Q(item_kind=kind, item_id__in=items)
    return matching


def _excerpt(body: str, words: list[str]) -> str:
    text = " ".join(re.sub(_REFERENCES, " ", body).split())
    if not text:
        return ""
    folded = text.casefold()
    positions = [folded.find(word.casefold()) for word in words]
    start = min((p for p in positions if p >= 0), default=0)
    begin = max(start - EXCERPT_RADIUS, 0)
    end = min(start + EXCERPT_RADIUS, len(text))
    return f"{'…' if begin else ''}{text[begin:end].strip()}{'…' if end < len(text) else ''}"
