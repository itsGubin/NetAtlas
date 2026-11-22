import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import assign_ip_address, create_subnet
from netatlas.devices.services import create_device, create_interface, get_device
from netatlas.pages.services import get_item_page
from netatlas.places.services import create_site


def root():
    return authenticate(username="root", password="correct-horse-battery")


def server():
    site = create_site(root(), name="Home")
    create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    port = create_interface(root(), device.id, name="eth0", kind="physical")
    address = assign_ip_address(root(), port.id, "10.0.0.5")
    return device, port, address


@pytest.mark.django_db
def test_anonymous_requests_are_refused(client):
    assert client.get("/api/delete-preview/device/1/").status_code == 401


@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR])
def test_preview_lists_the_impact_and_changes_nothing(client_with_role, role):
    client = client_with_role(role)
    device, port, address = server()

    response = client.get(f"/api/delete-preview/device/{device.id}/")

    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["item_id"], body["item_label"]) == ("device", device.id, "server")
    assert body["blocked"] == []
    assert body["deleted"] == [
        {"kind": "interface", "id": port.id, "label": "server eth0", "effect": "deleted"},
        {
            "kind": "page",
            "id": get_item_page(root(), "device", device.id).id,
            "label": "server",
            "effect": "deleted",
        },
    ]
    assert body["changed"] == [
        {"kind": "ip-address", "id": address.id, "label": "10.0.0.5", "effect": "becomes Reserved"}
    ]
    assert body["moved"] == []
    assert len(body["fingerprint"]) == 64
    assert get_device(root(), device.id).name == "server"


def test_viewer_cannot_preview(client_with_role):
    client = client_with_role(Role.VIEWER)
    device, _port, _address = server()

    assert client.get(f"/api/delete-preview/device/{device.id}/").status_code == 403


@pytest.mark.parametrize("path", ["widget/1", "device/999999"])
def test_unknown_kind_or_item_is_not_found(client_with_role, path):
    client = client_with_role(Role.EDITOR)

    response = client.get(f"/api/delete-preview/{path}/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_blocked_delete_is_a_normal_preview_with_the_reason(client_with_role):
    client = client_with_role(Role.EDITOR)
    device, _port, _address = server()
    create_device(root(), name="vm-1", kind="virtual", parent_id=device.id)

    response = client.get(f"/api/delete-preview/device/{device.id}/")

    assert response.status_code == 200
    assert "vm-1" in response.json()["blocked"][0]
    assert response.json()["fingerprint"] == ""


def test_unknown_query_parameter_is_refused(client_with_role):
    client = client_with_role(Role.EDITOR)
    device, _port, _address = server()

    assert client.get(f"/api/delete-preview/device/{device.id}/?x=1").status_code == 400


def test_api_delete_still_needs_no_confirmation(client_with_role):
    client = client_with_role(Role.EDITOR)
    device, _port, address = server()

    assert client.delete(f"/api/devices/{device.id}/").status_code == 204
    assert client.get(f"/api/ip-addresses/{address.id}/").json()["status"] == "reserved"
