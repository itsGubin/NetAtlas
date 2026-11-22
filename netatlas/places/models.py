from django.db import models

from netatlas.tagging.models import TaggableModel


class Customer(TaggableModel):
    """An optional grouping of Sites belonging to one client organisation."""

    history_kind = "customer"

    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Site(TaggableModel):
    """A physical place where Devices live. Its Customer is optional."""

    history_kind = "site"

    name = models.CharField(max_length=100)
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="sites"
    )

    def __str__(self):
        return self.name
