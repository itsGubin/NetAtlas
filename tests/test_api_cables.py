import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_interface,
    pair_interfaces,
)
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


def two_ports():
    site = create_site(root(), name="Home")
    first = create_device(root(), name="switch", kind="physical", site_id=site.id)
    second = create_device(root(), name="server", kind="physical", site_id=site.id)
    return (
        create_interface(root(), first.id, name="port12", kind="physical"),
        create_interface(root(), second.id, name="eth0", kind="physical"),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path", ["/api/cables/", "/api/cables/1/", "/api/interfaces/1/trace/"]
)
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_a_cable(client_with_role, role):
    client = client_with_role(role)
    near, far = two_ports()

    created = post_json(
        client,
        "/api/cables/",
        {"interface_a_id": near.id, "interface_b_id": far.id, "label": "A-114"},
    )
    assert created.status_code == 201
    cable_id = created.json()["id"]
    assert created.json() == {
        "id": cable_id,
        "label": "A-114",
        "interface_a_id": near.id,
        "interface_a_name": "port12",
        "device_a_id": near.device_id,
        "device_a_name": "switch",
        "interface_b_id": far.id,
        "interface_b_name": "eth0",
        "device_b_id": far.device_id,
        "device_b_name": "server",
    }
    assert client.get(f"/api/interfaces/{near.id}/").json()["peer_device_name"] == "server"

    assert patch_json(client, f"/api/cables/{cable_id}/", {"label": "B"}).json()["label"] == "B"
    assert client.get(f"/api/cables/{cable_id}/").json()["label"] == "B"

    assert client.delete(f"/api/cables/{cable_id}/").status_code == 204
    assert client.get(f"/api/cables/{cable_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    spare = create_interface(root(), near.device_id, name="spare", kind="physical")
    cable = create_cable(root(), near.id, far.id)
    body = {"interface_a_id": spare.id, "interface_b_id": far.id}

    assert client.get("/api/cables/").json()["count"] == 1
    assert client.get(f"/api/cables/{cable.id}/").status_code == 200
    assert client.get(f"/api/interfaces/{near.id}/trace/").status_code == 200
    assert post_json(client, "/api/cables/", body).status_code == 403
    assert patch_json(client, f"/api/cables/{cable.id}/", {"label": "x"}).status_code == 403
    assert client.delete(f"/api/cables/{cable.id}/").status_code == 403
    pair = {"interface_id": near.id}
    assert put_json(client, f"/api/interfaces/{spare.id}/pair/", pair).status_code == 403
    assert client.delete(f"/api/interfaces/{spare.id}/pair/").status_code == 403


def test_cables_filter_by_device(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()
    cable = create_cable(root(), near.id, far.id)
    other = create_device(root(), name="other", kind="physical", site_id=_site_of(near))

    assert client.get(f"/api/cables/?device={far.device_id}").json()["results"][0]["id"] == cable.id
    assert client.get(f"/api/cables/?device={other.id}").json()["count"] == 0
    assert client.get("/api/cables/?device=abc").status_code == 400
    assert client.get("/api/cables/?devise=1").status_code == 400


def _site_of(interface):
    from netatlas.devices.services import get_device

    return get_device(root(), interface.device_id).site_id


def test_rule_violations_are_field_errors(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()
    create_cable(root(), near.id, far.id)
    spare = create_interface(root(), near.device_id, name="spare", kind="physical")

    response = post_json(
        client, "/api/cables/", {"interface_a_id": spare.id, "interface_b_id": far.id}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "interface_b_id" in response.json()["error"]["fields"]


def test_pair_and_unpair(client_with_role):
    client = client_with_role(Role.EDITOR)
    front, _ = two_ports()
    rear = create_interface(root(), front.device_id, name="rear", kind="physical")

    paired = put_json(client, f"/api/interfaces/{front.id}/pair/", {"interface_id": rear.id})
    assert paired.status_code == 200
    assert paired.json()["paired_interface_name"] == "rear"

    assert client.delete(f"/api/interfaces/{front.id}/pair/").status_code == 204
    assert client.get(f"/api/interfaces/{rear.id}/").json()["paired_interface_id"] is None


def test_invalid_pair_is_a_field_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()

    response = put_json(client, f"/api/interfaces/{near.id}/pair/", {"interface_id": far.id})

    assert response.status_code == 400
    assert "interface_id" in response.json()["error"]["fields"]


def test_trace_returns_the_path(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    rear = create_interface(root(), far.device_id, name="rear", kind="physical")
    pair_interfaces(root(), far.id, rear.id)
    cable = create_cable(root(), near.id, far.id, label="A")

    body = client.get(f"/api/interfaces/{near.id}/trace/").json()

    assert body["is_loop"] is False
    assert [(h["device_name"], h["interface_name"]) for h in body["hops"]] == [
        ("switch", "port12"),
        ("server", "eth0"),
        ("server", "rear"),
    ]
    assert body["hops"][0]["cable_id"] == cable.id
    assert body["hops"][0]["is_start"] is True


def test_tracing_a_virtual_interface_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, _ = two_ports()
    virtual = create_interface(root(), near.device_id, name="lo0", kind="virtual")

    assert client.get(f"/api/interfaces/{virtual.id}/trace/").status_code == 400
