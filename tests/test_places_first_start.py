import pytest
from django.contrib.auth import authenticate
from django.core.management import call_command

from netatlas.accounts.services import ensure_initial_admin
from netatlas.history.services import item_history
from netatlas.places.services import list_sites, update_site

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


def test_first_start_creates_one_default_site_recorded_as_system(admin):
    call_command("ensure_default_site")

    sites = list_sites(admin)
    assert [s.name for s in sites] == ["Default"]
    created = item_history(admin, "site", sites[0].id)[0]
    assert (created.action, created.actor) == ("created", "system")


def test_restart_keeps_a_renamed_default_site(admin):
    call_command("ensure_default_site")
    site = list_sites(admin)[0]
    update_site(admin, site.id, name="Home")

    call_command("ensure_default_site")

    assert [s.name for s in list_sites(admin)] == ["Home"]
