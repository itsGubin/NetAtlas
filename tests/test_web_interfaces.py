import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import (
    create_device,
    create_interface,
    get_interface,
    list_interfaces,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def new_device():
    site = create_site(root(), name="Home")
    return create_device(root(), name="sw", kind="physical", site_id=site.id)


@pytest.mark.django_db
def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/interfaces/1/")

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=/interfaces/1/"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_adds_edits_and_deletes_an_interface(client_with_role, role):
    client = client_with_role(role)
    device = new_device()
    lag = create_interface(root(), device.id, name="lag0", kind="lag")

    response = client.post(
        f"/devices/{device.id}/interfaces/",
        {"name": "p1", "kind": "physical", "lag_id": lag.id},
    )
    assert response.status_code == 302
    assert response["Location"] == f"/devices/{device.id}/"
    interface = next(i for i in list_interfaces(root(), device.id) if i.name == "p1")
    assert interface.lag_id == lag.id

    client.post(
        f"/interfaces/{interface.id}/", {"name": "uplink", "kind": "physical", "lag_id": ""}
    )
    edited = get_interface(root(), interface.id)
    assert (edited.name, edited.lag_id) == ("uplink", None)

    response = confirm_delete(client, f"/interfaces/{interface.id}/delete/")
    assert response.status_code == 302
    assert response["Location"] == f"/devices/{device.id}/"
    assert [i.name for i in list_interfaces(root(), device.id)] == ["lag0"]


def test_device_page_links_interfaces_and_shows_their_lag(client_with_role):
    client = client_with_role(Role.VIEWER)
    device = new_device()
    lag = create_interface(root(), device.id, name="lag0", kind="lag")
    member = create_interface(root(), device.id, name="p1", kind="physical", lag_id=lag.id)

    page = client.get(f"/devices/{device.id}/").content.decode()

    assert f'href="/interfaces/{member.id}/"' in page
    assert f'<a href="/interfaces/{lag.id}/">lag0</a>' in page


def test_viewer_sees_interfaces_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    device = new_device()
    interface = create_interface(root(), device.id, name="p1", kind="physical")
    new = {"name": "p2", "kind": "physical"}

    response = client.get(f"/interfaces/{interface.id}/")
    assert response.status_code == 200
    assert response.content.decode().count('method="post"') == 1

    assert client.post(f"/devices/{device.id}/interfaces/", new).status_code == 403
    assert client.post(f"/interfaces/{interface.id}/", new).status_code == 403
    assert client.post(f"/interfaces/{interface.id}/delete/").status_code == 403


def test_invalid_new_interface_shows_the_error_on_the_device_page(client_with_role):
    client = client_with_role(Role.EDITOR)
    device = new_device()
    create_interface(root(), device.id, name="taken", kind="physical")

    response = client.post(
        f"/devices/{device.id}/interfaces/", {"name": "taken", "kind": "physical"}
    )

    page = response.content.decode()
    assert response.status_code == 400
    assert 'role="alert"' in page
    assert 'name="name" value="taken"' in page
    assert 'value="sw"' in page


def test_invalid_edit_shows_the_error_and_keeps_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)
    device = new_device()
    interface = create_interface(root(), device.id, name="p1", kind="physical")

    response = client.post(
        f"/interfaces/{interface.id}/", {"name": "p 1" + "x" * 70, "kind": "physical"}
    )

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert "x" * 70 in response.content.decode()


def test_edit_form_offers_only_other_lags_of_the_same_device(client_with_role):
    client = client_with_role(Role.EDITOR)
    device = new_device()
    lag = create_interface(root(), device.id, name="lag0", kind="lag")
    other_lag = create_interface(root(), device.id, name="lag1", kind="lag")
    plain = create_interface(root(), device.id, name="p1", kind="physical")

    page = client.get(f"/interfaces/{lag.id}/").content.decode()

    assert f'<option value="{lag.id}"' not in page
    assert f'<option value="{other_lag.id}"' in page
    assert f'<option value="{plain.id}"' not in page


def test_virtual_device_form_only_offers_virtual_interfaces(client_with_role):
    client = client_with_role(Role.EDITOR)
    host = new_device()
    vm = create_device(root(), name="vm", kind="virtual", parent_id=host.id)

    page = client.get(f"/devices/{vm.id}/").content.decode()

    assert '<option value="virtual"' in page
    assert '<option value="physical"' not in page
    assert '<option value="lag"' not in page


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/devices/{device}/interfaces/", {"name": "   ", "kind": "physical"}),
        ("/devices/", {"name": "   ", "kind": "physical", "site_id": "{site}"}),
        ("/sites/", {"name": "   "}),
        ("/customers/", {"name": "   "}),
        ("/device-types/", {"make": "   ", "model_name": "m", "rack_height": "1"}),
    ],
)
def test_a_name_of_only_spaces_is_a_form_error(client_with_role, path, data):
    client = client_with_role(Role.EDITOR)
    device = new_device()
    values = {
        key: str(device.site_id) if value == "{site}" else value for key, value in data.items()
    }

    response = client.post(path.format(device=device.id), values)

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_interface_view_shows_its_history_and_device(client_with_role):
    client = client_with_role(Role.VIEWER)
    device = new_device()
    interface = create_interface(root(), device.id, name="p1", kind="physical")

    page = client.get(f"/interfaces/{interface.id}/").content.decode()

    assert 'data-history-action="created"' in page
    assert f'href="/devices/{device.id}/"' in page
