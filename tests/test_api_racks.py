import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import create_device, create_rack, place_device
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


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/racks/", "/api/racks/1/", "/api/racks/1/elevation/"])
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_a_rack(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")

    created = post_json(client, "/api/racks/", {"name": "R1", "site_id": site.id, "height": 42})
    assert created.status_code == 201
    rack_id = created.json()["id"]
    assert created.json() == {
        "id": rack_id,
        "name": "R1",
        "site_id": site.id,
        "site_name": "Home",
        "height": 42,
    }

    assert patch_json(client, f"/api/racks/{rack_id}/", {"height": 24}).json()["height"] == 24
    assert client.get(f"/api/racks/{rack_id}/").json()["height"] == 24

    assert client.delete(f"/api/racks/{rack_id}/").status_code == 204
    assert client.get(f"/api/racks/{rack_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)
    placement = f"/api/devices/{device.id}/placement/"
    position = {"rack_id": rack.id, "unit": 1, "face": "front"}

    assert client.get("/api/racks/").json()["count"] == 1
    assert client.get(f"/api/racks/{rack.id}/elevation/").status_code == 200
    new = {"name": "R2", "site_id": site.id, "height": 4}
    assert post_json(client, "/api/racks/", new).status_code == 403
    assert patch_json(client, f"/api/racks/{rack.id}/", {"name": "x"}).status_code == 403
    assert client.delete(f"/api/racks/{rack.id}/").status_code == 403
    assert put_json(client, placement, position).status_code == 403
    assert client.delete(placement).status_code == 403


def test_racks_filter_by_site(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    create_rack(root(), name="R1", site_id=home.id, height=4)
    rack = create_rack(root(), name="R2", site_id=office.id, height=4)

    assert [r["id"] for r in client.get(f"/api/racks/?site={office.id}").json()["results"]] == [
        rack.id
    ]
    assert client.get("/api/racks/?site=abc").status_code == 400
    assert client.get("/api/racks/?sit=1").status_code == 400


def test_device_is_placed_and_removed(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)
    placement = f"/api/devices/{device.id}/placement/"

    placed = put_json(client, placement, {"rack_id": rack.id, "unit": 2, "face": "rear"})

    assert placed.status_code == 200
    body = placed.json()
    assert (body["rack_id"], body["rack_name"], body["rack_unit"], body["rack_face"]) == (
        rack.id,
        "R1",
        2,
        "rear",
    )
    assert client.delete(placement).status_code == 204
    assert client.get(f"/api/devices/{device.id}/").json()["rack_id"] is None


def test_zero_height_device_is_placed_without_a_position(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    pdu = create_device(root(), name="pdu", kind="physical", site_id=site.id, rack_height=0)

    response = put_json(client, f"/api/devices/{pdu.id}/placement/", {"rack_id": rack.id})

    assert response.status_code == 200
    assert response.json()["rack_unit"] is None


def test_overlap_is_a_field_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    first = create_device(root(), name="a", kind="physical", site_id=site.id)
    second = create_device(root(), name="b", kind="physical", site_id=site.id)
    place_device(root(), first.id, rack.id, unit=1, face="front")

    response = put_json(
        client,
        f"/api/devices/{second.id}/placement/",
        {"rack_id": rack.id, "unit": 1, "face": "front"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "unit" in response.json()["error"]["fields"]


def test_elevation_returns_both_faces(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=2)
    server = create_device(root(), name="srv", kind="physical", site_id=site.id, rack_height=2)
    pdu = create_device(root(), name="pdu", kind="physical", site_id=site.id, rack_height=0)
    place_device(root(), server.id, rack.id, unit=1, face="rear")
    place_device(root(), pdu.id, rack.id)

    body = client.get(f"/api/racks/{rack.id}/elevation/").json()

    assert body["rack"]["name"] == "R1"
    assert [u["device_name"] for u in body["front"]] == [None, None]
    assert body["rear"][0] == {
        "unit": 2,
        "device_id": server.id,
        "device_name": "srv",
        "span": 2,
        "is_covered": False,
    }
    assert body["rear"][1]["is_covered"] is True
    assert body["zero_height"] == [{"id": pdu.id, "name": "pdu"}]


def test_deleting_a_used_rack_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    device = create_device(root(), name="sw-1", kind="physical", site_id=site.id)
    place_device(root(), device.id, rack.id, unit=1, face="front")

    response = client.delete(f"/api/racks/{rack.id}/")

    assert response.status_code == 400
    assert "sw-1" in response.json()["error"]["message"]
