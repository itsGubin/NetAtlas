import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import create_device, create_device_type
from netatlas.places.services import create_site

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


@pytest.mark.django_db
def test_anonymous_requests_are_refused(client):
    assert client.get("/api/devices/").status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_a_device(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")
    template = create_device_type(
        root(),
        make="Acme",
        model_name="SW",
        rack_height=2,
        interfaces=[{"name": "p1", "kind": "physical"}],
    )

    created = post_json(
        client,
        "/api/devices/",
        {"name": "sw", "kind": "physical", "site_id": site.id, "device_type_id": template.id},
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["name"], body["site_name"], body["device_type_name"], body["rack_height"]) == (
        "sw",
        "Home",
        "Acme SW",
        2,
    )
    assert [(i["name"], i["kind"]) for i in body["interfaces"]] == [("p1", "physical")]

    renamed = patch_json(client, f"/api/devices/{body['id']}/", {"name": "core"})
    assert renamed.json()["name"] == "core"
    assert client.get(f"/api/devices/{body['id']}/").json()["name"] == "core"

    assert client.delete(f"/api/devices/{body['id']}/").status_code == 204
    assert client.get(f"/api/devices/{body['id']}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)

    assert client.get("/api/devices/").status_code == 200
    assert client.get(f"/api/devices/{device.id}/").status_code == 200
    assert client.get(f"/api/devices/{device.id}/virtual-devices/").status_code == 200
    new = {"name": "x", "kind": "physical", "site_id": site.id}
    assert post_json(client, "/api/devices/", new).status_code == 403
    assert patch_json(client, f"/api/devices/{device.id}/", {"name": "x"}).status_code == 403
    assert client.delete(f"/api/devices/{device.id}/").status_code == 403


def test_devices_filter_by_site_kind_and_parent(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    host = create_device(root(), name="host", kind="physical", site_id=home.id)
    create_device(root(), name="vm", kind="virtual", parent_id=host.id)
    create_device(root(), name="other", kind="physical", site_id=office.id)

    def names(query):
        return [d["name"] for d in client.get(f"/api/devices/?{query}").json()["results"]]

    assert names(f"site={office.id}") == ["other"]
    assert names("kind=virtual") == ["vm"]
    assert names(f"parent={host.id}") == ["vm"]
    for bad in ("site=abc", "kind=robot", "parent=abc", "sitee=1"):
        assert client.get(f"/api/devices/?{bad}").status_code == 400


def test_virtual_devices_endpoint_is_nested_by_depth(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    vm = create_device(root(), name="vm", kind="virtual", parent_id=host.id)
    container = create_device(root(), name="ct", kind="virtual", parent_id=vm.id)

    response = client.get(f"/api/devices/{host.id}/virtual-devices/")

    assert response.json()["results"] == [
        {"id": vm.id, "name": "vm", "parent_id": host.id, "depth": 1},
        {"id": container.id, "name": "ct", "parent_id": vm.id, "depth": 2},
    ]


def test_rule_violations_are_field_errors(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = post_json(
        client, "/api/devices/", {"name": "vm", "kind": "virtual", "site_id": site.id}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "parent_id" in response.json()["error"]["fields"]


def test_deleting_a_hypervisor_or_a_used_site_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_site(root(), name="Office")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    create_device(root(), name="vm", kind="virtual", parent_id=host.id)

    hypervisor = client.delete(f"/api/devices/{host.id}/")
    used_site = client.delete(f"/api/sites/{site.id}/")

    assert hypervisor.status_code == 400
    assert "vm" in hypervisor.json()["error"]["message"]
    assert used_site.status_code == 400


def test_kind_cannot_be_changed(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)

    response = patch_json(client, f"/api/devices/{device.id}/", {"kind": "virtual"})

    assert response.status_code == 400
    assert "kind" in response.json()["error"]["fields"]
    assert client.get(f"/api/devices/{device.id}/").json()["kind"] == "physical"
