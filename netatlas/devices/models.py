from django.db import models
from django.utils.translation import gettext_lazy as _

from netatlas.history.models import TrackedModel


class DeviceType(TrackedModel):
    """A reusable make and model. Devices copy from it, so edits never reach existing Devices."""

    history_kind = "device-type"

    make = models.CharField(max_length=100)
    # Not `model`: create_item takes the model class under that name.
    model_name = models.CharField(max_length=100)
    rack_height = models.PositiveSmallIntegerField(default=1)
    interfaces = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["make", "model_name"],
                name="unique_device_type_make_model",
                violation_error_message=_("A Device Type with this make and model already exists."),
            )
        ]

    def __str__(self):
        return f"{self.make} {self.model_name}"


class Device(TrackedModel):
    """Anything documented that has Interfaces or takes rack space, in exactly one Site."""

    class Kind(models.TextChoices):
        PHYSICAL = "physical", _("Physical")
        VIRTUAL = "virtual", _("Virtual")

    history_kind = "device"

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    site = models.ForeignKey("places.Site", on_delete=models.PROTECT, related_name="devices")
    # The Hypervisor of a Virtual Device. Any Device can be one, to any depth.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    # Where the Device was copied from. Later edits to the Device Type do not reach it.
    device_type = models.ForeignKey(
        DeviceType, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices"
    )
    rack_height = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "name"],
                name="unique_device_name_per_site",
                violation_error_message=_("A Device with this name already exists in this Site."),
            ),
            models.CheckConstraint(
                condition=models.Q(kind="physical", parent__isnull=True)
                | models.Q(kind="virtual", parent__isnull=False),
                name="only_virtual_devices_have_a_parent",
                violation_error_message=_("Only a Virtual Device has a Hypervisor, and it must."),
            ),
        ]

    def __str__(self):
        return self.name


class Interface(TrackedModel):
    """A network connection point belonging to exactly one Device."""

    class Kind(models.TextChoices):
        PHYSICAL = "physical", _("Physical")
        VIRTUAL = "virtual", _("Virtual")
        LAG = "lag", _("LAG")

    history_kind = "interface"

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="interfaces")
    name = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # The LAG this Physical Interface is bundled into. Members outlive their LAG.
    lag = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "name"],
                name="unique_interface_name_per_device",
                violation_error_message=_("This Device already has an Interface with this name."),
            )
        ]

    def __str__(self):
        return f"{self.device.name} {self.name}"
