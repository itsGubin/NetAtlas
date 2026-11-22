"""Delete previews (ADR 0005).

A preview runs the real delete and rolls it back, so it cannot disagree with what a delete does.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor
from netatlas.addressing.models import DhcpRange, IpAddress, Subnet, Vlan
from netatlas.addressing.services import (
    delete_dhcp_range,
    delete_ip_address,
    delete_subnet,
    delete_vlan,
    subnet_delete_moves,
)
from netatlas.devices.models import Cable, Device, DeviceType, Interface, Rack
from netatlas.devices.services import (
    delete_cable,
    delete_device,
    delete_device_type,
    delete_interface,
    delete_rack,
)
from netatlas.history.services import capture_changes, rehearse
from netatlas.pages.models import Page
from netatlas.pages.services import broken_live_fields, delete_page
from netatlas.places.models import Customer, Site
from netatlas.places.services import delete_customer, delete_site
from netatlas.tagging.models import Tag
from netatlas.tagging.services import delete_tag

_DELETERS = {
    "customer": (Customer, delete_customer),
    "site": (Site, delete_site),
    "device": (Device, delete_device),
    "interface": (Interface, delete_interface),
    "rack": (Rack, delete_rack),
    "vlan": (Vlan, delete_vlan),
    "subnet": (Subnet, delete_subnet),
    "dhcp-range": (DhcpRange, delete_dhcp_range),
    "ip-address": (IpAddress, delete_ip_address),
    "cable": (Cable, delete_cable),
    "device-type": (DeviceType, delete_device_type),
    "tag": (Tag, delete_tag),
    "page": (Page, delete_page),
}
DELETABLE_KINDS = tuple(_DELETERS)


class StaleDelete(Exception):
    """What a delete would affect is no longer what the person confirmed."""


@dataclass(frozen=True)
class Impact:
    kind: str
    id: int | None
    label: str
    effect: str


@dataclass(frozen=True)
class DeletePreview:
    kind: str
    item_id: int
    item_label: str
    deleted: list[Impact]
    changed: list[Impact]
    moved: list[Impact]
    # Why the delete is refused. Empty if it can go ahead.
    blocked: list[str]
    fingerprint: str
    # Live Fields on Pages that stay, which will show as broken.
    broken_live_fields: list[Impact] = field(default_factory=list)


def preview_delete(actor, kind: str, item_id: int) -> DeletePreview:
    """Everything a delete would remove or change. Nothing is kept."""
    require_editor(actor)
    model, delete = _deleter(kind)
    label = _label(model, item_id)
    try:
        with rehearse() as captured:
            moves = _moves(kind, item_id)
            delete(actor, item_id)
            broken = _broken_live_fields(captured)
    except ValidationError as error:
        return DeletePreview(kind, item_id, label, [], [], [], error.messages, "")

    others = [c for c in captured if (c.kind, c.item_id) != (kind, item_id)]
    return DeletePreview(
        kind=kind,
        item_id=item_id,
        item_label=label,
        deleted=_sorted(
            Impact(c.kind, c.item_id, c.label, _("deleted"))
            for c in others
            if c.action == "deleted"
        ),
        changed=_sorted(
            Impact(c.kind, c.item_id, c.label, _effect(c)) for c in others if c.action == "updated"
        ),
        moved=[
            Impact(move_kind, None, move_label, _move_effect(target))
            for move_kind, move_label, target in moves
        ],
        blocked=[],
        fingerprint=_fingerprint(captured, moves, broken),
        broken_live_fields=[
            Impact(
                "page",
                entry.page_id,
                entry.page_title,
                _("Live Field breaks: %(item)s, %(field)s")
                % {"item": entry.item_label, "field": entry.field_label},
            )
            for entry in broken
        ],
    )


def confirm_delete(actor, kind: str, item_id: int, fingerprint: str) -> None:
    """Delete, but only if it affects exactly what the confirmed preview showed."""
    require_editor(actor)
    _model, delete = _deleter(kind)
    # Checked after the real delete, in its transaction, so nothing can slip in between.
    with transaction.atomic(), capture_changes() as captured:
        moves = _moves(kind, item_id)
        delete(actor, item_id)
        expected = _fingerprint(captured, moves, _broken_live_fields(captured))
        # Compared as bytes, because the submitted value can be any text.
        if not hmac.compare_digest(expected.encode(), fingerprint.encode()):
            raise StaleDelete


def _deleter(kind: str):
    if kind not in _DELETERS:
        raise Http404
    return _DELETERS[kind]


def _label(model, item_id: int) -> str:
    item = model.objects.filter(pk=item_id).first()
    if item is None:
        raise Http404
    return str(item)


def _broken_live_fields(captured) -> list:
    """Asked after the delete, so Pages deleted along with the item don't count."""
    return broken_live_fields(
        (change.kind, change.item_id, change.label)
        for change in captured
        if change.action == "deleted"
    )


def _moves(kind: str, item_id: int) -> list:
    return subnet_delete_moves(item_id) if kind == "subnet" else []


def _sorted(impacts) -> list[Impact]:
    return sorted(impacts, key=lambda impact: (impact.kind, impact.label, impact.id))


def _move_effect(target: str | None) -> str:
    return _("moves to %(cidr)s") % {"cidr": target} if target else _("moves to the top level")


def _effect(change) -> str:
    effects = {
        ("ip-address", "interface"): _("becomes Reserved"),
        ("interface", "lag"): _("leaves the LAG"),
        ("interface", "paired_with"): _("loses its pair"),
        ("site", "customer"): _("loses its Customer"),
        ("device", "device_type"): _("keeps its copy, without the Device Type"),
    }
    for field in change.fields:
        if field.field == "tags":
            return _("loses this Tag")
        if (change.kind, field.field) in effects:
            return effects[(change.kind, field.field)]
    return _("changes")


def _fingerprint(captured, moves, broken) -> str:
    """Identifies exactly what a delete does, including every old value it records."""
    changes = sorted(
        json.dumps(
            [
                c.action,
                c.kind,
                c.item_id,
                c.label,
                sorted([f.field, f.old, f.new] for f in c.fields),
            ],
            cls=DjangoJSONEncoder,
        )
        for c in captured
    )
    references = [[b.page_id, b.kind, b.item_id, b.field] for b in broken]
    payload = json.dumps([changes, moves, references], cls=DjangoJSONEncoder)
    return hashlib.sha256(payload.encode()).hexdigest()
