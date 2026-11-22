"""History: every change to a documented item, recorded with the change itself (ADR 0003).

Services write documented items only through create_item, update_item and delete_item.
Each runs in one transaction, so a change and its History are kept or lost together.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models.deletion import Collector
from django.http import Http404

from netatlas.accounts.services import require_editor, require_role
from netatlas.history.models import Change, FieldChange, TrackedModel, allow_tracked_writes


@dataclass(frozen=True)
class FieldChangeSummary:
    field: str
    old: Any
    new: Any


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    item_kind: str
    item_id: int
    item_label: str
    action: str
    actor: str
    at: datetime
    changes: list[FieldChangeSummary]


def create_item(actor, model, **values):
    require_editor(actor)
    with transaction.atomic(), allow_tracked_writes():
        item = model(**values)
        item.full_clean()
        item.save()
        after = _snapshot(item)
        _record(actor, item, Change.Action.CREATED, {f: (None, v) for f, v in after.items()})
    return item


def update_item(actor, item, **values):
    """Change an item and record the fields that actually changed."""
    require_editor(actor)
    model = type(item)
    writable = {name for field in _fields(model) for name in (field.name, field.attname)}
    unknown = set(values) - writable
    if unknown:
        raise TypeError(f"{model.__name__} has no field {', '.join(sorted(unknown))}")

    with transaction.atomic(), allow_tracked_writes():
        # Read the old values under a lock, so History is right even for a stale `item`.
        current = model._base_manager.select_for_update().get(pk=item.pk)
        stored = _stored_values(current)
        before = _snapshot(current)
        for name, value in values.items():
            setattr(current, name, value)
        current.full_clean()

        changed = [name for name, value in _stored_values(current).items() if stored[name] != value]
        if changed:
            current.save(update_fields=changed)
            _record_difference(actor, current, before)
    return current


def delete_item(actor, item) -> None:
    """Delete an item, recording it, everything removed with it, and everything it changes."""
    require_editor(actor)
    with transaction.atomic(), allow_tracked_writes():
        current = type(item)._base_manager.select_for_update().get(pk=item.pk)
        removed = _removed_with(current)
        removed_keys = {(type(entry), entry.pk) for entry in removed}
        survivors = [
            (survivor, _snapshot(survivor))
            for survivor in _dependents(removed)
            if (type(survivor), survivor.pk) not in removed_keys
        ]

        for entry in removed:
            before = _snapshot(entry)
            _record(actor, entry, Change.Action.DELETED, {f: (v, None) for f, v in before.items()})
        current.delete()

        for survivor, before in survivors:
            fresh = type(survivor)._base_manager.get(pk=survivor.pk)
            _record_difference(actor, fresh, before)


def item_history(actor, kind: str, item_id: int) -> list[HistoryEntry]:
    """An item's History, newest first. It stays available after the item is deleted."""
    require_role(actor)
    changes = (
        Change.objects.filter(item_kind=kind, item_id=item_id)
        .prefetch_related("field_changes")
        .order_by("-at", "-pk")
    )
    entries = [_entry(change) for change in changes]
    if not entries:
        raise Http404
    return entries


def _removed_with(item) -> list:
    """The tracked items a delete of `item` removes, including `item` itself."""
    collector = Collector(using=item._state.db, origin=item)
    collector.collect([item])
    removed = [
        instance
        for model, instances in collector.data.items()
        if issubclass(model, TrackedModel)
        for instance in instances
    ]
    for queryset in collector.fast_deletes:
        if issubclass(queryset.model, TrackedModel):
            removed.extend(queryset)
    return removed


def _dependents(items) -> list:
    """Tracked items pointing at `items`, which a delete may change (such as SET_NULL)."""
    found = {}
    for item in items:
        for relation in type(item)._meta.get_fields(include_hidden=True):
            if not (relation.auto_created and not relation.concrete):
                continue
            if not (relation.one_to_many or relation.one_to_one):
                continue
            if issubclass(relation.related_model, TrackedModel):
                manager = relation.related_model._base_manager
                for dependent in manager.filter(**{relation.field.name: item}):
                    found[(type(dependent), dependent.pk)] = dependent
    return list(found.values())


def _record_difference(actor, item, before) -> None:
    after = _snapshot(item)
    changes = {f: (before[f], after[f]) for f in after if before[f] != after[f]}
    if changes:
        _record(actor, item, Change.Action.UPDATED, changes)


def _record(actor, item, action, changes) -> None:
    change = Change.objects.create(
        item_kind=item.history_kind,
        item_id=item.pk,
        item_label=str(item)[:255],
        action=action,
        actor=actor,
        actor_name=actor.get_username(),
    )
    FieldChange.objects.bulk_create(
        FieldChange(change=change, field=field, old=old, new=new)
        for field, (old, new) in changes.items()
    )


def _fields(model) -> list:
    return [field for field in model._meta.concrete_fields if not field.primary_key]


def _stored_values(item) -> dict[str, Any]:
    return {field.name: field.value_from_object(item) for field in _fields(type(item))}


def _snapshot(item) -> dict[str, Any]:
    """The item's recorded fields, as the JSON values History stores."""
    model = type(item)
    return {
        field.name: json.loads(json.dumps(field.value_from_object(item), cls=DjangoJSONEncoder))
        for field in _fields(model)
        if field.name not in model.history_exclude
    }


def _entry(change) -> HistoryEntry:
    return HistoryEntry(
        id=change.pk,
        item_kind=change.item_kind,
        item_id=change.item_id,
        item_label=change.item_label,
        action=change.action,
        actor=change.actor_name,
        at=change.at,
        changes=[
            FieldChangeSummary(field=row.field, old=row.old, new=row.new)
            for row in sorted(change.field_changes.all(), key=lambda row: row.pk)
        ],
    )
