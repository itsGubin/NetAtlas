"""Tags on documented items."""

from collections import Counter
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.addressing.models import DhcpRange, IpAddress, Subnet, Vlan
from netatlas.devices.models import Cable, Device, DeviceType, Interface, Rack
from netatlas.history.services import create_item, delete_item, update_item
from netatlas.pages.models import Page
from netatlas.places.models import Customer, Site
from netatlas.system.locks import lock_inventory
from netatlas.tagging.models import Tag

MAX_NAME_LENGTH = 50

_TAGGABLE = {
    "customer": Customer,
    "site": Site,
    "device": Device,
    "interface": Interface,
    "rack": Rack,
    "vlan": Vlan,
    "subnet": Subnet,
    "dhcp-range": DhcpRange,
    "ip-address": IpAddress,
    "cable": Cable,
    "device-type": DeviceType,
    "page": Page,
}
TAGGABLE_KINDS = tuple(_TAGGABLE)


@dataclass(frozen=True)
class TagRef:
    id: int
    name: str


@dataclass(frozen=True)
class TagSummary:
    id: int
    name: str
    usage: int


@dataclass(frozen=True)
class TaggedItem:
    kind: str
    id: int
    label: str


@dataclass(frozen=True)
class TagUsage:
    tag: TagSummary
    items: list[TaggedItem]


def list_tags(actor) -> list[TagSummary]:
    """All Tags alphabetically, each with how many items carry it."""
    require_role(actor)
    usage = Counter()
    for model in _TAGGABLE.values():
        usage.update(model.tags.through.objects.values_list("tag_id", flat=True))
    return [
        TagSummary(id=tag.pk, name=tag.name, usage=usage[tag.pk])
        for tag in Tag.objects.order_by("key")
    ]


def get_tag(actor, tag_id: int) -> TagSummary:
    require_role(actor)
    tag = _find_tag(tag_id)
    return TagSummary(id=tag.pk, name=tag.name, usage=len(_tagged_items(tag)))


def tag_usage(actor, tag_id: int) -> TagUsage:
    require_role(actor)
    tag = _find_tag(tag_id)
    items = _tagged_items(tag)
    return TagUsage(
        tag=TagSummary(id=tag.pk, name=tag.name, usage=len(items)),
        items=[
            TaggedItem(kind=kind, id=item.pk, label=str(item))
            for kind, item in sorted(items, key=lambda pair: (pair[0], str(pair[1]), pair[1].pk))
        ],
    )


def create_tag(actor, name: str) -> TagSummary:
    require_editor(actor)
    clean = _clean_name(name)
    with transaction.atomic():
        lock_inventory()
        if Tag.objects.filter(key=clean.casefold()).exists():
            raise _duplicate()
        tag = create_item(actor, Tag, name=clean, key=clean.casefold())
    return TagSummary(id=tag.pk, name=tag.name, usage=0)


def update_tag(actor, tag_id: int, name: str) -> TagSummary:
    """Rename a Tag. Changing only its case is allowed."""
    require_editor(actor)
    clean = _clean_name(name)
    with transaction.atomic():
        lock_inventory()
        tag = _find_tag(tag_id)
        if Tag.objects.filter(key=clean.casefold()).exclude(pk=tag.pk).exists():
            raise _duplicate()
        update_item(actor, tag, name=clean, key=clean.casefold())
    return get_tag(actor, tag_id)


def delete_tag(actor, tag_id: int) -> None:
    """Delete a Tag, recording its removal in the History of every item that carried it."""
    require_editor(actor)
    with transaction.atomic():
        lock_inventory()
        tag = _find_tag(tag_id)
        for _kind, item in _tagged_items(tag):
            remaining = [pk for pk in item.tags.values_list("pk", flat=True) if pk != tag.pk]
            update_item(actor, item, many_to_many={"tags": remaining})
        delete_item(actor, tag)


def tags_of(actor, kind: str, item_id: int) -> list[TagRef]:
    require_role(actor)
    item = _find_item(kind, item_id)
    return [TagRef(id=tag.pk, name=tag.name) for tag in item.tags.order_by("key")]


def add_tag(actor, kind: str, item_id: int, name: str) -> TagRef:
    """Put a Tag on an item, creating it on first use in the spelling given."""
    require_editor(actor)
    model = _model(kind)
    clean = _clean_name(name)
    with transaction.atomic():
        lock_inventory()
        item = _find_in(model, item_id)
        tag = Tag.objects.filter(key=clean.casefold()).first()
        if tag is None:
            tag = create_item(actor, Tag, name=clean, key=clean.casefold())
        current = list(item.tags.values_list("pk", flat=True))
        if tag.pk not in current:
            update_item(actor, item, many_to_many={"tags": [*current, tag.pk]})
    return TagRef(id=tag.pk, name=tag.name)


def remove_tag(actor, kind: str, item_id: int, tag_id: int) -> None:
    require_editor(actor)
    model = _model(kind)
    with transaction.atomic():
        lock_inventory()
        item = _find_in(model, item_id)
        current = list(item.tags.values_list("pk", flat=True))
        if tag_id not in current:
            raise Http404
        update_item(
            actor, item, many_to_many={"tags": [pk for pk in current if pk != tag_id]}
        )


def filter_by_tags(kind: str, items: list, names: list[str]) -> list:
    """The summaries whose item carries every named Tag, in any spelling."""
    if any("\x00" in name for name in names):
        return []
    matching = _model(kind).objects.all()
    for name in names:
        matching = matching.filter(tags__key=name.strip().casefold())
    ids = set(matching.values_list("pk", flat=True))
    return [item for item in items if item.id in ids]


def _model(kind: str):
    if kind not in _TAGGABLE:
        raise Http404
    return _TAGGABLE[kind]


def _find_item(kind: str, item_id: int):
    return _find_in(_model(kind), item_id)


def _find_in(model, item_id: int):
    item = model.objects.filter(pk=item_id).first()
    if item is None:
        raise Http404
    return item


def _find_tag(tag_id: int):
    tag = Tag.objects.filter(pk=tag_id).first()
    if tag is None:
        raise Http404
    return tag


def _tagged_items(tag) -> list[tuple[str, object]]:
    return [
        (kind, item) for kind, model in _TAGGABLE.items() for item in model.objects.filter(tags=tag)
    ]


def _clean_name(name) -> str:
    clean = name.strip() if isinstance(name, str) else ""
    if not clean or "\x00" in clean:
        raise ValidationError({"name": [_("Enter a Tag name.")]})
    if len(clean) > MAX_NAME_LENGTH:
        message = _("A Tag name has at most %(limit)d characters.") % {"limit": MAX_NAME_LENGTH}
        raise ValidationError({"name": [message]})
    return clean


def _duplicate() -> ValidationError:
    return ValidationError({"name": [_("A Tag with this name already exists.")]})
