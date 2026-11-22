import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_ip_address,
    create_subnet,
)
from netatlas.devices.services import create_device, create_interface
from netatlas.places.services import create_site

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


def plan():
    site = create_site(root(), name="Home")
    subnet = create_subnet(root(), cidr="192.168.1.0/24", site_id=site.id)
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    port = create_interface(root(), device.id, name="eth0", kind="physical")
    return site, subnet, port


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/api/ip-addresses/", "/api/ip-addresses/1/", "/api/dhcp-ranges/", "/api/subnets/1/usage/"],
)
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_an_ip_address(client_with_role, role):
    client = client_with_role(role)
    site, subnet, port = plan()

    created = post_json(
        client,
        "/api/ip-addresses/",
        {"address": "192.168.1.10", "site_id": site.id, "description": "Printer"},
    )
    assert created.status_code == 201
    address_id = created.json()["id"]
    assert created.json() == {
        "id": address_id,
        "address": "192.168.1.10",
        "version": 4,
        "status": "reserved",
        "site_id": site.id,
        "site_name": "Home",
        "subnet_id": subnet.id,
        "subnet_cidr": "192.168.1.0/24",
        "interface_id": None,
        "interface_name": None,
        "device_id": None,
        "device_name": None,
        "description": "Printer",
    }

    assigned = patch_json(client, f"/api/ip-addresses/{address_id}/", {"interface_id": port.id})
    assert (assigned.json()["status"], assigned.json()["device_name"]) == ("assigned", "server")
    released = patch_json(client, f"/api/ip-addresses/{address_id}/", {"interface_id": None})
    assert released.json()["status"] == "reserved"

    assert client.delete(f"/api/ip-addresses/{address_id}/").status_code == 204
    assert client.get(f"/api/ip-addresses/{address_id}/").status_code == 404


def test_address_is_assigned_through_its_interface(client_with_role):
    client = client_with_role(Role.EDITOR)
    _site, _subnet, port = plan()

    response = post_json(
        client, f"/api/interfaces/{port.id}/ip-addresses/", {"address": "192.168.1.10"}
    )

    assert response.status_code == 201
    assert response.json()["interface_id"] == port.id
    interface = client.get(f"/api/interfaces/{port.id}/").json()
    assert [a["address"] for a in interface["ip_addresses"]] == ["192.168.1.10"]


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_a_dhcp_range(client_with_role, role):
    client = client_with_role(role)
    _site, subnet, _port = plan()

    created = post_json(
        client,
        "/api/dhcp-ranges/",
        {"subnet_id": subnet.id, "start": "192.168.1.100", "end": "192.168.1.200"},
    )
    assert created.status_code == 201
    range_id = created.json()["id"]
    assert created.json() == {
        "id": range_id,
        "subnet_id": subnet.id,
        "subnet_cidr": "192.168.1.0/24",
        "start": "192.168.1.100",
        "end": "192.168.1.200",
        "size": "101",
        "description": "",
    }

    changed = patch_json(client, f"/api/dhcp-ranges/{range_id}/", {"end": "192.168.1.150"})
    assert changed.json()["size"] == "51"

    assert client.delete(f"/api/dhcp-ranges/{range_id}/").status_code == 204
    assert client.get(f"/api/dhcp-ranges/{range_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site, subnet, port = plan()
    address = create_ip_address(root(), address="192.168.1.10", site_id=site.id)
    dhcp = create_dhcp_range(root(), subnet.id, "192.168.1.100", "192.168.1.200")

    assert client.get("/api/ip-addresses/").json()["count"] == 1
    assert client.get("/api/dhcp-ranges/").json()["count"] == 1
    assert client.get(f"/api/subnets/{subnet.id}/usage/").status_code == 200
    new_address = {"address": "192.168.1.11", "site_id": site.id}
    new_range = {"subnet_id": subnet.id, "start": "192.168.1.1", "end": "192.168.1.2"}
    assert post_json(client, "/api/ip-addresses/", new_address).status_code == 403
    assert patch_json(
        client, f"/api/ip-addresses/{address.id}/", {"description": "x"}
    ).status_code == 403
    assert client.delete(f"/api/ip-addresses/{address.id}/").status_code == 403
    assert post_json(
        client, f"/api/interfaces/{port.id}/ip-addresses/", {"address": "192.168.1.11"}
    ).status_code == 403
    assert post_json(client, "/api/dhcp-ranges/", new_range).status_code == 403
    assert patch_json(
        client, f"/api/dhcp-ranges/{dhcp.id}/", {"end": "192.168.1.201"}
    ).status_code == 403
    assert client.delete(f"/api/dhcp-ranges/{dhcp.id}/").status_code == 403


def test_addresses_filter(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, subnet, port = plan()
    other = create_site(root(), name="Office")
    create_subnet(root(), cidr="10.0.0.0/24", site_id=other.id)
    create_ip_address(root(), address="192.168.1.10", site_id=site.id)
    assign_ip_address(root(), port.id, "192.168.1.11")
    create_ip_address(root(), address="10.0.0.1", site_id=other.id)

    def addresses(query):
        response = client.get(f"/api/ip-addresses/?{query}")
        return [a["address"] for a in response.json()["results"]]

    assert addresses(f"site={other.id}") == ["10.0.0.1"]
    assert addresses(f"subnet={subnet.id}") == ["192.168.1.10", "192.168.1.11"]
    assert addresses(f"interface={port.id}") == ["192.168.1.11"]
    assert addresses("status=reserved") == ["10.0.0.1", "192.168.1.10"]
    for bad in ("site=abc", "subnet=abc", "interface=abc", "status=free", "address=1"):
        assert client.get(f"/api/ip-addresses/?{bad}").status_code == 400


def test_ranges_filter_by_subnet(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, subnet, _port = plan()
    other = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    create_dhcp_range(root(), subnet.id, "192.168.1.100", "192.168.1.200")
    dhcp = create_dhcp_range(root(), other.id, "10.0.0.10", "10.0.0.20")

    results = client.get(f"/api/dhcp-ranges/?subnet={other.id}").json()["results"]

    assert [r["id"] for r in results] == [dhcp.id]
    assert client.get("/api/dhcp-ranges/?subnet=abc").status_code == 400
    assert client.get("/api/dhcp-ranges/?start=1").status_code == 400


@pytest.mark.parametrize("address", ["nonsense", "192.168.1.0", "172.16.0.1", "192.168.1.5/24"])
def test_invalid_address_is_a_field_error(client_with_role, address):
    client = client_with_role(Role.EDITOR)
    site, _subnet, _port = plan()

    response = post_json(client, "/api/ip-addresses/", {"address": address, "site_id": site.id})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "address" in response.json()["error"]["fields"]


def test_usage_returns_counts_as_text_for_huge_numbers(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    subnet = create_subnet(root(), cidr="2001:db8::/32", site_id=site.id)
    create_ip_address(root(), address="2001:db8::", site_id=site.id)

    body = client.get(f"/api/subnets/{subnet.id}/usage/").json()

    assert body["subnet"]["cidr"] == "2001:db8::/32"
    assert (body["assigned"], body["reserved"]) == (0, 1)
    assert body["free"] == str(2**96 - 1)
    assert body["next_free"] == "2001:db8::1"
    assert [a["address"] for a in body["ip_addresses"]] == ["2001:db8::"]


def test_deleting_a_subnet_that_would_orphan_addresses_is_refused(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, subnet, _port = plan()
    create_ip_address(root(), address="192.168.1.10", site_id=site.id)

    response = client.delete(f"/api/subnets/{subnet.id}/")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
