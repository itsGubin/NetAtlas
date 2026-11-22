from django.db import models
from django.utils.translation import gettext_lazy as _

from netatlas.history.models import TrackedModel


class Subnet(TrackedModel):
    """A CIDR block in one Site (ADR 0002). Nesting is derived from the addresses."""

    history_kind = "subnet"

    site = models.ForeignKey("places.Site", on_delete=models.PROTECT, related_name="subnets")
    # Always canonical, so two spellings of one IPv6 network cannot both be stored.
    cidr = models.CharField(max_length=64)

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
