import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import create_device, create_interface
from netatlas.places.services import create_site

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


def new_device(kind="physical"):
    site = create_site(root(), name="Home")
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)
    if kind == "virtual":
        return create_device(root(), name="vm", kind="virtual", parent_id=device.id)
    return device


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/devices/1/interfaces/", "/api/interfaces/1/"])
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_an_interface(client_with_role, role):
    client = client_with_role(role)
    device = new_device()
    lag = create_interface(root(), device.id, name="lag0", kind="lag")

    created = post_json(
        client,
        f"/api/devices/{device.id}/interfaces/",
        {"name": "p1", "kind": "physical", "lag_id": lag.id},
    )
    assert created.status_code == 201
    interface_id = created.json()["id"]
    assert created.json() == {
        "id": interface_id,
        "device_id": device.id,
        "name": "p1",
        "kind": "physical",
        "lag_id": lag.id,
        "lag_name": "lag0",
        "cable_id": None,
        "peer_interface_id": None,
        "peer_interface_name": None,
        "peer_device_id": None,
        "peer_device_name": None,
        "paired_interface_id": None,
        "paired_interface_name": None,
    }

    changed = patch_json(client, f"/api/interfaces/{interface_id}/", {"lag_id": None})
    assert changed.json()["lag_id"] is None
    assert client.get(f"/api/interfaces/{interface_id}/").json()["lag_name"] is None

    assert client.delete(f"/api/interfaces/{interface_id}/").status_code == 204
    assert client.get(f"/api/interfaces/{interface_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    device = new_device()
    interface = create_interface(root(), device.id, name="p1", kind="physical")
    path = f"/api/interfaces/{interface.id}/"

    assert client.get(f"/api/devices/{device.id}/interfaces/").json()["count"] == 1
    assert client.get(path).status_code == 200
    new = {"name": "p2", "kind": "physical"}
    assert post_json(client, f"/api/devices/{device.id}/interfaces/", new).status_code == 403
    assert patch_json(client, path, {"name": "p2"}).status_code == 403
    assert client.delete(path).status_code == 403


def test_unknown_device_is_not_found(client_with_role):
    client = client_with_role(Role.EDITOR)
    new = {"name": "p1", "kind": "physical"}

    assert client.get("/api/devices/999999/interfaces/").status_code == 404
    assert post_json(client, "/api/devices/999999/interfaces/", new).status_code == 404


def test_list_rejects_unknown_filters(client_with_role):
    client = client_with_role(Role.EDITOR)
    device = new_device()

    assert client.get(f"/api/devices/{device.id}/interfaces/?kind=lag").status_code == 400


def test_rule_violations_are_field_errors(client_with_role):
    client = client_with_role(Role.EDITOR)
    vm = new_device("virtual")

    response = post_json(
        client, f"/api/devices/{vm.id}/interfaces/", {"name": "p1", "kind": "physical"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "kind" in response.json()["error"]["fields"]


def test_device_detail_includes_lag_membership(client_with_role):
    client = client_with_role(Role.VIEWER)
    device = new_device()
    lag = create_interface(root(), device.id, name="lag0", kind="lag")
    create_interface(root(), device.id, name="p1", kind="physical", lag_id=lag.id)

    interfaces = client.get(f"/api/devices/{device.id}/").json()["interfaces"]

    assert [(i["name"], i["lag_name"]) for i in interfaces] == [("lag0", None), ("p1", "lag0")]
