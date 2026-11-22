from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from netatlas.tagging.models import TaggableModel


class DeviceType(TaggableModel):
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


class Rack(TaggableModel):
    """A rack of mounting units within a Site, numbered from 1 at the bottom."""

    history_kind = "rack"

    site = models.ForeignKey("places.Site", on_delete=models.PROTECT, related_name="racks")
    name = models.CharField(max_length=100)
    height = models.PositiveSmallIntegerField(
        default=42, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "name"],
                name="unique_rack_name_per_site",
                violation_error_message=_("A Rack with this name already exists in this Site."),
            )
        ]

    def __str__(self):
        return self.name


class Device(TaggableModel):
    """Anything documented that has Interfaces or takes rack space, in exactly one Site."""

    class Kind(models.TextChoices):
        PHYSICAL = "physical", _("Physical")
        VIRTUAL = "virtual", _("Virtual")

    class Face(models.TextChoices):
        FRONT = "front", _("Front")
        REAR = "rear", _("Rear")

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
    rack = models.ForeignKey(
        Rack, null=True, blank=True, on_delete=models.PROTECT, related_name="devices"
    )
    # The Rack Position: lowest unit and Face. A zero-height Device is in the Rack without one.
    rack_unit = models.PositiveSmallIntegerField(null=True, blank=True)
    rack_face = models.CharField(max_length=8, choices=Face.choices, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "name"],
                name="unique_device_name_per_site",
                violation_error_message=_("A Device with this name already exists in this Site."),
            ),
            models.CheckConstraint(
                condition=models.Q(rack_unit__isnull=True, rack_face="")
                | models.Q(
                    rack__isnull=False, rack_unit__gte=1, rack_face__in=["front", "rear"]
                ),
                name="rack_position_is_complete",
                violation_error_message=_("A Rack Position needs a Rack, a unit and a Face."),
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


class Interface(TaggableModel):
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
    # The other side of a pass-through, such as a patch panel's front and rear. Set on both.
    paired_with = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    untagged_vlan = models.ForeignKey(
        "addressing.Vlan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="untagged_interfaces",
    )
    tagged_vlans = models.ManyToManyField(
        "addressing.Vlan", blank=True, related_name="tagged_interfaces"
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


class Cable(TaggableModel):
    """A physical link joining exactly two Physical Interfaces, stored lowest ID first."""

    history_kind = "cable"

    interface_a = models.OneToOneField(
        Interface, on_delete=models.CASCADE, related_name="cable_as_a"
    )
    interface_b = models.OneToOneField(
        Interface, on_delete=models.CASCADE, related_name="cable_as_b"
    )
    label = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(interface_a__lt=models.F("interface_b")),
                name="cable_ends_are_ordered_and_different",
                violation_error_message=_("A Cable needs two different Interfaces."),
            )
        ]

    def __str__(self):
        ends = f"{self.interface_a} – {self.interface_b}"
        return f"{self.label}: {ends}" if self.label else ends
