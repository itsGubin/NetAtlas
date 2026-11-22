import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import create_subnet, create_vlan
from netatlas.devices.services import create_device, create_interface, set_interface_vlans
from netatlas.places.services import create_site

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


def put_json(client, path, data):
    return client.put(path, data, content_type="application/json")


def new_port(site):
    device = create_device(root(), name="switch", kind="physical", site_id=site.id)
    return create_interface(root(), device.id, name="p1", kind="physical")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/vlans/", "/api/vlans/1/", "/api/vlans/1/usage/"])
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_a_vlan(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")

    created = post_json(
        client, "/api/vlans/", {"number": 10, "site_id": site.id, "name": "Guest"}
    )
    assert created.status_code == 201
    vlan_id = created.json()["id"]
    assert created.json() == {
        "id": vlan_id,
        "number": 10,
        "name": "Guest",
        "site_id": site.id,
        "site_name": "Home",
    }

    assert patch_json(client, f"/api/vlans/{vlan_id}/", {"number": 20}).json()["number"] == 20
    assert client.get(f"/api/vlans/{vlan_id}/").json()["name"] == "Guest"

    assert client.delete(f"/api/vlans/{vlan_id}/").status_code == 204
    assert client.get(f"/api/vlans/{vlan_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id)
    port = new_port(site)
    path = f"/api/vlans/{vlan.id}/"
    body = {"untagged_vlan_id": vlan.id, "tagged_vlan_ids": []}

    assert client.get("/api/vlans/").json()["count"] == 1
    assert client.get(path).status_code == 200
    assert client.get(f"{path}usage/").status_code == 200
    new = {"number": 20, "site_id": site.id}
    assert post_json(client, "/api/vlans/", new).status_code == 403
    assert patch_json(client, path, {"name": "x"}).status_code == 403
    assert client.delete(path).status_code == 403
    assert put_json(client, f"/api/interfaces/{port.id}/vlans/", body).status_code == 403


def test_vlans_filter_by_site(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    create_vlan(root(), number=10, site_id=home.id)
    vlan = create_vlan(root(), number=10, site_id=office.id)

    results = client.get(f"/api/vlans/?site={office.id}").json()["results"]

    assert [v["id"] for v in results] == [vlan.id]
    assert client.get("/api/vlans/?site=abc").status_code == 400
    assert client.get("/api/vlans/?number=10").status_code == 400


@pytest.mark.parametrize("number", [0, 4095])
def test_invalid_number_is_a_field_error(client_with_role, number):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = post_json(client, "/api/vlans/", {"number": number, "site_id": site.id})

    assert response.status_code == 400
    assert "number" in response.json()["error"]["fields"]


def test_subnet_is_bound_and_unbound(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id, name="Guest")

    created = post_json(
        client, "/api/subnets/", {"cidr": "10.0.0.0/24", "site_id": site.id, "vlan_id": vlan.id}
    )
    assert (created.json()["vlan_number"], created.json()["vlan_name"]) == (10, "Guest")

    changed = patch_json(client, f"/api/subnets/{created.json()['id']}/", {"vlan_id": None})
    assert changed.json()["vlan_id"] is None


def test_interface_vlans_are_set_and_shown(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    untagged = create_vlan(root(), number=10, site_id=site.id, name="Guest")
    tagged = create_vlan(root(), number=20, site_id=site.id)
    port = new_port(site)

    response = put_json(
        client,
        f"/api/interfaces/{port.id}/vlans/",
        {"untagged_vlan_id": untagged.id, "tagged_vlan_ids": [tagged.id]},
    )

    assert response.status_code == 200
    assert response.json()["untagged_vlan"] == {"id": untagged.id, "number": 10, "name": "Guest"}
    assert [v["number"] for v in response.json()["tagged_vlans"]] == [20]
    assert client.get(f"/api/devices/{port.device_id}/").json()["interfaces"][0][
        "untagged_vlan"
    ]["number"] == 10


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"untagged_vlan_id": 999999, "tagged_vlan_ids": []}, "untagged_vlan_id"),
        ({"untagged_vlan_id": None, "tagged_vlan_ids": [999999]}, "tagged_vlan_ids"),
        ({"untagged_vlan_id": None, "tagged_vlan_ids": ["abc"]}, "tagged_vlan_ids"),
        ({"untagged_vlan_id": None, "tagged_vlan_ids": "5"}, "tagged_vlan_ids"),
        ({"tagged_vlan_ids": []}, "untagged_vlan_id"),
        ({"untagged_vlan_id": None}, "tagged_vlan_ids"),
    ],
)
def test_invalid_interface_vlans_are_field_errors(client_with_role, body, field):
    client = client_with_role(Role.EDITOR)
    port = new_port(create_site(root(), name="Home"))

    response = put_json(client, f"/api/interfaces/{port.id}/vlans/", body)

    assert response.status_code == 400
    fields = response.json()["error"]["fields"]
    assert all(isinstance(message, str) for message in fields[field])


def test_usage_lists_subnets_and_interfaces(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id)
    port = new_port(site)
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id, vlan_id=vlan.id)
    set_interface_vlans(root(), port.id, None, [vlan.id])

    body = client.get(f"/api/vlans/{vlan.id}/usage/").json()

    assert body["vlan"]["number"] == 10
    assert body["subnets"] == [{"id": subnet.id, "cidr": "10.0.0.0/24"}]
    assert body["interfaces"] == [
        {
            "id": port.id,
            "interface_name": "p1",
            "device_id": port.device_id,
            "device_name": "switch",
            "mode": "tagged",
        }
    ]


def test_deleting_a_used_vlan_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id)
    set_interface_vlans(root(), new_port(site).id, vlan.id, [])

    response = client.delete(f"/api/vlans/{vlan.id}/")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
