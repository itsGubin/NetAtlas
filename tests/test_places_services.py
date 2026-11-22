import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.history.services import item_history
from netatlas.places.services import (
    create_customer,
    create_site,
    delete_customer,
    delete_site,
    list_sites,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def editor(admin):
    return create_user(admin, "editor", "another-long-password", Role.EDITOR)


def test_editor_creates_a_site_belonging_to_a_customer(editor):
    customer = create_customer(editor, name="Acme")

    site = create_site(editor, name="Zurich Office", customer_id=customer.id)

    assert [(s.name, s.customer_id, s.customer_name) for s in list_sites(editor)] == [
        ("Zurich Office", customer.id, "Acme")
    ]
    created = item_history(editor, "site", site.id)[0]
    assert (created.action, created.actor, created.item_label) == (
        "created",
        "editor",
        "Zurich Office",
    )


def test_deleting_a_customer_keeps_its_sites_and_records_the_change(editor):
    customer = create_customer(editor, name="Acme")
    site = create_site(editor, name="Zurich Office", customer_id=customer.id)

    delete_customer(editor, customer.id)

    assert [(s.name, s.customer_id) for s in list_sites(editor)] == [("Zurich Office", None)]
    latest = item_history(editor, "site", site.id)[0]
    assert latest.action == "updated"
    assert [(c.field, c.old, c.new) for c in latest.changes] == [("customer", customer.id, None)]


def test_the_last_site_cannot_be_deleted(editor):
    first = create_site(editor, name="Home")
    second = create_site(editor, name="Office")

    delete_site(editor, first.id)

    with pytest.raises(ValidationError):
        delete_site(editor, second.id)
    assert [s.name for s in list_sites(editor)] == ["Office"]
