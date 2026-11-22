"""Customers and Sites."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.history.services import create_item, create_system_item, delete_item, update_item
from netatlas.places.models import Customer, Site

DEFAULT_SITE_NAME = "Default"


@dataclass(frozen=True)
class CustomerSummary:
    id: int
    name: str


@dataclass(frozen=True)
class SiteSummary:
    id: int
    name: str
    customer_id: int | None
    customer_name: str | None


def create_customer(actor, name: str) -> CustomerSummary:
    customer = create_item(actor, Customer, name=name)
    return CustomerSummary(id=customer.pk, name=customer.name)


def has_customers() -> bool:
    return Customer.objects.exists()


def list_customers(actor) -> list[CustomerSummary]:
    require_role(actor)
    customers = Customer.objects.order_by("name", "pk")
    return [CustomerSummary(id=c.pk, name=c.name) for c in customers]


def get_customer(actor, customer_id: int) -> CustomerSummary:
    require_role(actor)
    customer = _find(Customer, customer_id)
    return CustomerSummary(id=customer.pk, name=customer.name)


def update_customer(actor, customer_id: int, **values) -> CustomerSummary:
    require_editor(actor)
    customer = update_item(actor, _find(Customer, customer_id), **values)
    return CustomerSummary(id=customer.pk, name=customer.name)


def delete_customer(actor, customer_id: int) -> None:
    """Delete a Customer. Its Sites stay, without a Customer."""
    require_editor(actor)
    delete_item(actor, _find(Customer, customer_id))


def create_site(actor, name: str, customer_id: int | None = None) -> SiteSummary:
    site = create_item(actor, Site, name=name, customer_id=customer_id)
    return _site_summary(site)


def update_site(actor, site_id: int, **values) -> SiteSummary:
    require_editor(actor)
    return _site_summary(update_item(actor, _find(Site, site_id), **values))


def get_site(actor, site_id: int) -> SiteSummary:
    require_role(actor)
    return _site_summary(_find(Site, site_id))


def ensure_default_site() -> bool:
    """Create the default Site on first start. Does nothing once any Site exists."""
    if Site.objects.exists():
        return False
    create_system_item(Site, name=DEFAULT_SITE_NAME)
    return True


def delete_site(actor, site_id: int) -> None:
    """Delete a Site. An install always keeps at least one."""
    require_editor(actor)
    with transaction.atomic():
        # Locking every Site stops two concurrent deletes from removing the last one.
        site_ids = list(Site.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
        if site_id not in site_ids:
            raise Http404
        if len(site_ids) == 1:
            raise ValidationError(_("NetAtlas must keep at least one Site."))
        try:
            delete_item(actor, Site.objects.get(pk=site_id))
        except ProtectedError as error:
            message = _("This Site still has Devices or Racks. Move or delete them first.")
            raise ValidationError(message) from error


def list_sites(actor, customer_id: int | None = None) -> list[SiteSummary]:
    require_role(actor)
    sites = Site.objects.select_related("customer").order_by("name", "pk")
    if customer_id is not None:
        sites = sites.filter(customer_id=customer_id)
    return [_site_summary(site) for site in sites]


def _find(model, item_id: int):
    item = model.objects.filter(pk=item_id).first()
    if item is None:
        raise Http404
    return item


def _site_summary(site) -> SiteSummary:
    customer = site.customer
    return SiteSummary(
        id=site.pk,
        name=site.name,
        customer_id=customer.pk if customer else None,
        customer_name=customer.name if customer else None,
    )
