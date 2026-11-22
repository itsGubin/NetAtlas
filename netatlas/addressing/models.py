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


class IpAddress(TrackedModel):
    """One address in a Site. Assigned with an Interface, Reserved without (ADR 0005).

    Its Subnet is derived: always the most specific one containing it.
    """

    history_kind = "ip-address"

    site = models.ForeignKey("places.Site", on_delete=models.PROTECT, related_name="ip_addresses")
    # Always canonical, so two spellings of one IPv6 address cannot both be stored.
    address = models.CharField(max_length=45)
    interface = models.ForeignKey(
        "devices.Interface",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ip_addresses",
    )
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "address"],
                name="unique_ip_address_per_site",
                violation_error_message=_("This IP Address already exists in this Site."),
            )
        ]

    def __str__(self):
        return self.address


class DhcpRange(TrackedModel):
    """A block of addresses in a Subnet handed out dynamically, inclusive at both ends."""

    history_kind = "dhcp-range"

    subnet = models.ForeignKey(Subnet, on_delete=models.PROTECT, related_name="dhcp_ranges")
    start = models.CharField(max_length=45)
    end = models.CharField(max_length=45)
    description = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.start} – {self.end}"
