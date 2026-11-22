from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from netatlas.history.models import TrackedModel


class Vlan(TrackedModel):
    """A numbered layer-2 broadcast domain in one Site (ADR 0002)."""

    history_kind = "vlan"

    site = models.ForeignKey("places.Site", on_delete=models.PROTECT, related_name="vlans")
    number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(4094)]
    )
    name = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "number"],
                name="unique_vlan_number_per_site",
                violation_error_message=_("This VLAN number already exists in this Site."),
            )
        ]

    def __str__(self):
        return f"VLAN {self.number} {self.name}".strip()


class Subnet(TrackedModel):
    """A CIDR block in one Site (ADR 0002). Nesting is derived from the addresses."""

    history_kind = "subnet"

    site = models.ForeignKey("places.Site", on_delete=models.PROTECT, related_name="subnets")
    # Always canonical, so two spellings of one IPv6 network cannot both be stored.
    cidr = models.CharField(max_length=64)
    vlan = models.ForeignKey(
        Vlan, null=True, blank=True, on_delete=models.PROTECT, related_name="subnets"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "cidr"],
                name="unique_subnet_per_site",
                violation_error_message=_("This Subnet already exists in this Site."),
            )
        ]

    def __str__(self):
        return self.cidr
