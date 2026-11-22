import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import (
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    create_vlan,
)
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
)
from netatlas.places.services import create_customer, create_site
from netatlas.tagging.services import add_tag, create_tag


def root():
    return authenticate(username="root", password="correct-horse-battery")


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/tags/", "/api/tags/1/", "/api/items/device/1/tags/"])
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR])
def test_writer_manages_a_tag(client_with_role, role):
    client = client_with_role(role)

    created = post_json(client, "/api/tags/", {"name": "prod"})
    assert created.status_code == 201
    tag_id = created.json()["id"]
    assert created.json() == {"id": tag_id, "name": "prod", "usage": 0}

    assert patch_json(client, f"/api/tags/{tag_id}/", {"name": "Prod"}).json()["name"] == "Prod"
    assert client.get(f"/api/tags/{tag_id}/").json() == {
        "tag": {"id": tag_id, "name": "Prod", "usage": 0},
        "items": [],
    }

    assert client.delete(f"/api/tags/{tag_id}/").status_code == 204
    assert client.get(f"/api/tags/{tag_id}/").status_code == 404


def test_writer_adds_and_removes_a_tag_on_an_item(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    path = f"/api/items/site/{site.id}/tags/"

    added = post_json(client, path, {"name": "prod"})
    assert added.status_code == 201
    tag_id = added.json()["id"]
    assert client.get(path).json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [{"id": tag_id, "name": "prod"}],
    }

    assert client.delete(f"{path}{tag_id}/").status_code == 204
    assert client.get(path).json()["count"] == 0


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    tag = add_tag(root(), "site", site.id, "prod")
    path = f"/api/items/site/{site.id}/tags/"

    assert client.get("/api/tags/").json()["count"] == 1
    assert client.get(f"/api/tags/{tag.id}/").status_code == 200
    assert client.get(path).json()["count"] == 1
    assert post_json(client, "/api/tags/", {"name": "dev"}).status_code == 403
    assert patch_json(client, f"/api/tags/{tag.id}/", {"name": "x"}).status_code == 403
    assert client.delete(f"/api/tags/{tag.id}/").status_code == 403
    assert post_json(client, path, {"name": "dev"}).status_code == 403
    assert client.delete(f"{path}{tag.id}/").status_code == 403


@pytest.mark.parametrize("path", ["/api/items/widget/1/tags/", "/api/items/device/999999/tags/"])
def test_unknown_kind_or_item_is_not_found(client_with_role, path):
    client = client_with_role(Role.EDITOR)

    assert client.get(path).status_code == 404
    assert post_json(client, path, {"name": "prod"}).status_code == 404


def test_invalid_or_duplicate_name_is_a_field_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    create_tag(root(), "prod")

    for name in ("", "x" * 51, "PROD"):
        response = post_json(client, "/api/tags/", {"name": name})
        assert response.status_code == 400
        assert "name" in response.json()["error"]["fields"]


LISTS = {
    "customer": "/api/customers/",
    "site": "/api/sites/",
    "device": "/api/devices/",
    "rack": "/api/racks/",
    "vlan": "/api/vlans/",
    "subnet": "/api/subnets/",
    "dhcp-range": "/api/dhcp-ranges/",
    "ip-address": "/api/ip-addresses/",
    "cable": "/api/cables/",
    "device-type": "/api/device-types/",
}


@pytest.mark.parametrize("kind", [*LISTS, "interface"])
def test_every_list_filters_by_all_given_tags(client_with_role, kind):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    eth = [
        create_interface(root(), device.id, name=f"eth{n}", kind="physical") for n in range(4)
    ]
    makers = {
        "customer": lambda n: create_customer(root(), name=f"c{n}"),
        "site": lambda n: create_site(root(), name=f"s{n}"),
        "device": lambda n: create_device(root(), name=f"d{n}", kind="physical", site_id=site.id),
        "interface": lambda n: eth[n],
        "rack": lambda n: create_rack(root(), name=f"r{n}", site_id=site.id, height=4),
        "vlan": lambda n: create_vlan(root(), number=n + 1, site_id=site.id),
        "subnet": lambda n: create_subnet(root(), cidr=f"10.{n + 1}.0.0/24", site_id=site.id),
        "dhcp-range": lambda n: create_dhcp_range(
            root(), subnet.id, f"10.0.0.{n * 10 + 100}", f"10.0.0.{n * 10 + 105}"
        ),
        "ip-address": lambda n: create_ip_address(
            root(), address=f"10.0.0.{n + 1}", site_id=site.id
        ),
        "cable": lambda n: create_cable(root(), eth[n * 2].id, eth[n * 2 + 1].id),
        "device-type": lambda n: create_device_type(root(), make="Acme", model_name=f"m{n}"),
    }
    one = makers[kind](0)
    two = makers[kind](1)
    add_tag(root(), kind, one.id, "prod")
    add_tag(root(), kind, two.id, "prod")
    add_tag(root(), kind, two.id, "dmz")
    path = f"/api/devices/{device.id}/interfaces/" if kind == "interface" else LISTS[kind]

    def ids(query):
        return {item["id"] for item in client.get(f"{path}?{query}").json()["results"]}

    assert ids("tag=PROD") == {one.id, two.id}
    assert ids("tag=prod&tag=dmz") == {two.id}
    assert ids("tag=unknown") == set()
    assert client.get(f"{path}?tag=").status_code == 400


def test_tag_filter_combines_with_other_filters(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    local = create_device(root(), name="a", kind="physical", site_id=home.id)
    remote = create_device(root(), name="b", kind="physical", site_id=office.id)
    add_tag(root(), "device", local.id, "prod")
    add_tag(root(), "device", remote.id, "prod")

    results = client.get(f"/api/devices/?tag=prod&site={office.id}").json()["results"]

    assert [d["id"] for d in results] == [remote.id]


def test_delete_preview_covers_tags(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    tag = add_tag(root(), "site", site.id, "prod")

    body = client.get(f"/api/delete-preview/tag/{tag.id}/").json()

    assert [(c["kind"], c["effect"]) for c in body["changed"]] == [("site", "loses this Tag")]
