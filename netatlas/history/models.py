from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

_tracked_write = ContextVar("netatlas_tracked_write", default=False)


class UntrackedWrite(RuntimeError):
    """A documented item was written without going through the History services."""


@contextmanager
def allow_tracked_writes():
    """For the History services only: they record every write made inside this block."""
    token = _tracked_write.set(True)
    try:
        yield
    finally:
        _tracked_write.reset(token)


def _require_tracked_write():
    if not _tracked_write.get():
        raise UntrackedWrite("Write documented items through netatlas.history.services.")


class TrackedQuerySet(models.QuerySet):
    def update(self, **kwargs):
        _require_tracked_write()
        return super().update(**kwargs)

    update.alters_data = True

    def delete(self):
        _require_tracked_write()
        return super().delete()

    delete.alters_data = True
    delete.queryset_only = True

    def bulk_create(self, *args, **kwargs):
        _require_tracked_write()
        return super().bulk_create(*args, **kwargs)

    bulk_create.alters_data = True

    def bulk_update(self, *args, **kwargs):
        _require_tracked_write()
        return super().bulk_update(*args, **kwargs)

    bulk_update.alters_data = True


class TrackedModel(models.Model):
    """Base of every documented item and Page. Its changes are recorded in History.

    Direct writes raise UntrackedWrite. Use create_item, update_item and delete_item.
    """

    history_kind: str = ""
    history_exclude: tuple[str, ...] = ()

    objects = TrackedQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        _require_tracked_write()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _require_tracked_write()
        return super().delete(*args, **kwargs)


class Change(models.Model):
    """One change to one item. It outlives the item, so it holds the item's kind, ID and label."""

    class Action(models.TextChoices):
        CREATED = "created"
        UPDATED = "updated"
        DELETED = "deleted"

    item_kind = models.CharField(max_length=50)
    item_id = models.BigIntegerField()
    item_label = models.CharField(max_length=255)
    action = models.CharField(max_length=16, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(max_length=150)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["item_kind", "item_id", "-at"])]


class FieldChange(models.Model):
    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name="field_changes")
    field = models.CharField(max_length=100)
    old = models.JSONField(null=True, encoder=DjangoJSONEncoder)
    new = models.JSONField(null=True, encoder=DjangoJSONEncoder)
