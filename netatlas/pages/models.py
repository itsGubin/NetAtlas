from django.db import models
from django.db.models import Q

from netatlas.addressing.models import DhcpRange, IpAddress, Subnet, Vlan
from netatlas.devices.models import Cable, Device, DeviceType, Interface, Rack
from netatlas.history.models import TrackedModel
from netatlas.places.models import Customer, Site
from netatlas.tagging.models import TaggableModel

ITEM_PAGE_MODELS = {
    model.history_kind: model for model in (Customer, Site, Rack, Device, Vlan, Subnet)
}
MENTIONABLE_MODELS = {
    **ITEM_PAGE_MODELS,
    **{
        model.history_kind: model
        for model in (Interface, DhcpRange, IpAddress, Cable, DeviceType)
    },
}
# What each kind's name is built from, so listing many names stays one query.
LABEL_RELATIONS = {
    "interface": ("device",),
    "cable": ("interface_a__device", "interface_b__device"),
}


def with_labels(kind: str):
    """All items of a kind, ready for `str()`."""
    items = MENTIONABLE_MODELS[kind].objects.all()
    relations = LABEL_RELATIONS.get(kind)
    return items.select_related(*relations) if relations else items


class Page(TaggableModel):
    """A piece of documentation: an item's Item Page, or a titled Standalone Page (ADR 0004)."""

    history_kind = "page"

    # Only a Standalone Page has its own title. An Item Page is named after its item.
    title = models.CharField(max_length=200, blank=True)
    # On an Item Page, `body` is built from its Page Template and `sections`.
    body = models.TextField(blank=True)
    sections = models.JSONField(default=dict, blank=True)
    item_kind = models.CharField(max_length=20, blank=True)
    item_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item_kind", "item_id"],
                condition=Q(item_id__isnull=False),
                name="one_item_page_per_item",
            ),
            models.CheckConstraint(
                condition=(Q(item_id__isnull=True, item_kind="") & ~Q(title=""))
                | (Q(item_id__isnull=False, title="") & ~Q(item_kind="")),
                name="page_is_item_page_or_standalone",
            ),
        ]

    def __str__(self):
        if self.item_id is None:
            return self.title
        item = MENTIONABLE_MODELS[self.item_kind].objects.filter(pk=self.item_id).first()
        return str(item) if item else f"{self.item_kind} {self.item_id}"


class PageTemplate(TrackedModel):
    """The skeleton of Item Pages: a kind's default, or a Device Type's override."""

    history_kind = "page-template"

    name = models.CharField(max_length=100)
    item_kind = models.CharField(max_length=20)
    device_type = models.OneToOneField(
        DeviceType,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="page_template",
    )
    body = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item_kind"],
                condition=Q(device_type__isnull=True),
                name="one_default_page_template_per_kind",
            ),
            models.CheckConstraint(
                condition=Q(device_type__isnull=True) | Q(item_kind="device"),
                name="page_template_override_is_for_devices",
            ),
        ]

    def __str__(self):
        return self.name


class Mention(models.Model):
    """A Page's link to an item. Derived from the Page's body whenever it is saved."""

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="mentions")
    item_kind = models.CharField(max_length=20)
    item_id = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "item_kind", "item_id"], name="unique_mention_per_page"
            )
        ]
        indexes = [models.Index(fields=["item_kind", "item_id"])]
