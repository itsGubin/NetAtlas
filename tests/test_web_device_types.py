import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import create_device_type, list_device_types
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]
NEW_TYPE = {
    "make": "Acme",
    "model_name": "SW-3",
    "rack_height": "1",
    "interfaces": "port[1-2] physical\nlo0 virtual\n\nmgmt0",
}


def root():
    return authenticate(username="root", password="correct-horse-battery")


@pytest.mark.django_db
def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/device-types/")

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=/device-types/"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_device_type(client_with_role, role):
    client = client_with_role(role)

    assert client.post("/device-types/", NEW_TYPE).status_code == 302
    created = list_device_types(root())[0]
    assert created.interfaces == [
        {"name": "port1", "kind": "physical"},
        {"name": "port2", "kind": "physical"},
        {"name": "lo0", "kind": "virtual"},
        {"name": "mgmt0", "kind": "physical"},
    ]

    edit_page = client.get(f"/device-types/{created.id}/").content.decode()
    assert "port1 physical\nport2 physical\nlo0 virtual\nmgmt0 physical" in edit_page

    client.post(
        f"/device-types/{created.id}/",
        {**NEW_TYPE, "rack_height": "0", "interfaces": "port[1-3] physical"},
    )
    edited = list_device_types(root())[0]
    assert (edited.rack_height, len(edited.interfaces)) == (0, 3)

    assert confirm_delete(client, f"/device-types/{created.id}/delete/").status_code == 302
    assert list_device_types(root()) == []


def test_viewer_sees_device_types_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    item = create_device_type(root(), make="Acme", model_name="SW")

    for path in ("/device-types/", f"/device-types/{item.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count("<form") == 1

    assert client.post("/device-types/", NEW_TYPE).status_code == 403
    assert client.post(f"/device-types/{item.id}/", NEW_TYPE).status_code == 403
    assert client.post(f"/device-types/{item.id}/delete/").status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"interfaces": "port[9-1] physical"},
        {"interfaces": "port1 wifi"},
        {"interfaces": "port1 physical extra"},
        {"rack_height": "abc"},
        {"rack_height": "-1"},
    ],
)
def test_invalid_form_shows_the_error_and_keeps_the_input(client_with_role, change):
    client = client_with_role(Role.EDITOR)

    response = client.post("/device-types/", {**NEW_TYPE, **change})

    page = response.content.decode()
    assert response.status_code == 400
    assert 'role="alert"' in page
    assert 'value="SW-3"' in page
    assert next(iter(change.values())) in page
    assert list_device_types(root()) == []


def test_navigation_links_to_device_types(client_with_role):
    page = client_with_role(Role.VIEWER).get("/").content.decode()

    assert 'href="/device-types/"' in page


def test_item_view_shows_its_history(client_with_role):
    client = client_with_role(Role.VIEWER)
    item = create_device_type(root(), make="Acme", model_name="SW")

    page = client.get(f"/device-types/{item.id}/").content.decode()

    assert 'data-history-action="created"' in page
