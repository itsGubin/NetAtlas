import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import (
    create_device,
    create_device_type,
    get_device,
    list_devices,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


@pytest.mark.django_db
def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/devices/")

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=/devices/"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_device(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")
    template = create_device_type(
        root(), make="Acme", model_name="SW", interfaces=[{"name": "p1", "kind": "physical"}]
    )

    response = client.post(
        "/devices/",
        {"name": "sw", "kind": "physical", "site_id": site.id, "device_type_id": template.id},
    )
    assert response.status_code == 302
    device = list_devices(root())[0]
    assert [i.name for i in device.interfaces] == ["p1"]

    client.post(f"/devices/{device.id}/", {"name": "core", "site_id": site.id, "rack_height": "3"})
    edited = get_device(root(), device.id)
    assert (edited.name, edited.rack_height) == ("core", 3)

    assert confirm_delete(client, f"/devices/{device.id}/delete/").status_code == 302
    assert list_devices(root()) == []


def test_writer_creates_a_virtual_device_without_choosing_a_site(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)

    client.post("/devices/", {"name": "vm", "kind": "virtual", "parent_id": host.id})

    vm = next(d for d in list_devices(root()) if d.name == "vm")
    assert (vm.parent_id, vm.site_id) == (host.id, site.id)


def test_editing_a_virtual_device_changes_its_hypervisor(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    first = create_device(root(), name="first", kind="physical", site_id=site.id)
    second = create_device(root(), name="second", kind="physical", site_id=site.id)
    vm = create_device(root(), name="vm", kind="virtual", parent_id=first.id)

    client.post(f"/devices/{vm.id}/", {"name": "vm", "parent_id": second.id, "rack_height": "0"})

    assert get_device(root(), vm.id).parent_id == second.id


def test_edit_form_offers_only_valid_hypervisors(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    vm = create_device(root(), name="vm", kind="virtual", parent_id=host.id)
    nested = create_device(root(), name="nested", kind="virtual", parent_id=vm.id)

    page = client.get(f"/devices/{vm.id}/").content.decode()

    assert f'<option value="{host.id}" selected>' in page
    assert f'<option value="{vm.id}"' not in page
    assert f'<option value="{nested.id}"' not in page


def test_viewer_sees_devices_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)

    for path in ("/devices/", f"/devices/{device.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count("<form") == 1

    new = {"name": "x", "kind": "physical", "site_id": site.id}
    assert client.post("/devices/", new).status_code == 403
    assert client.post(f"/devices/{device.id}/", new).status_code == 403
    assert client.post(f"/devices/{device.id}/delete/").status_code == 403


def test_site_selection_is_hidden_while_only_one_site_exists(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    page = client.get("/devices/").content.decode()
    assert '<select name="site_id"' not in page
    assert f'type="hidden" name="site_id" value="{site.id}"' in page

    create_site(root(), name="Office")

    assert '<select name="site_id"' in client.get("/devices/").content.decode()


def test_hypervisor_view_lists_virtual_devices_nested(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    vm = create_device(root(), name="vm-one", kind="virtual", parent_id=host.id)
    create_device(root(), name="ct-one", kind="virtual", parent_id=vm.id)

    page = client.get(f"/devices/{host.id}/").content.decode()

    assert 'data-depth="1"' in page
    assert 'data-depth="2"' in page
    assert page.index("vm-one") < page.index("ct-one")


def test_invalid_form_shows_the_error_and_keeps_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)
    create_site(root(), name="Home")

    response = client.post("/devices/", {"name": "kept-name", "kind": "virtual"})

    page = response.content.decode()
    assert response.status_code == 400
    assert 'role="alert"' in page
    assert 'value="kept-name"' in page


def test_deleting_a_hypervisor_shows_why(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    create_device(root(), name="vm-one", kind="virtual", parent_id=host.id)

    response = client.post(f"/devices/{host.id}/delete/")

    assert response.status_code == 400
    assert "Move or delete its Virtual Devices first" in response.content.decode()


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/devices/", {"name": "a\x00b", "kind": "physical"}),
        ("/sites/", {"name": "a\x00b"}),
        ("/customers/", {"name": "a\x00b"}),
        ("/device-types/", {"make": "a\x00b", "model_name": "m", "rack_height": "1"}),
    ],
)
def test_a_nul_character_in_a_name_is_a_form_error(client_with_role, path, data):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = client.post(path, {**data, "site_id": site.id})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_navigation_links_to_devices(client_with_role):
    assert 'href="/devices/"' in client_with_role(Role.VIEWER).get("/").content.decode()


def test_item_view_shows_its_history(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)

    page = client.get(f"/devices/{device.id}/").content.decode()

    assert 'data-history-action="created"' in page
