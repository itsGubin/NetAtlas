import pytest

from netatlas.accounts.roles import Role

WRITERS = [Role.ADMIN, Role.EDITOR]
NEW_TYPE = {
    "make": "Acme",
    "model_name": "SW-2",
    "rack_height": 1,
    "interfaces": [{"name": "port[1-2]", "kind": "physical"}],
}


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


@pytest.mark.django_db
def test_anonymous_requests_are_refused(client):
    assert client.get("/api/device-types/").status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_device_types(client_with_role, role):
    client = client_with_role(role)

    created = post_json(client, "/api/device-types/", NEW_TYPE)
    assert created.status_code == 201
    type_id = created.json()["id"]
    assert created.json() == {
        "id": type_id,
        "make": "Acme",
        "model_name": "SW-2",
        "rack_height": 1,
        "interfaces": [
            {"name": "port1", "kind": "physical"},
            {"name": "port2", "kind": "physical"},
        ],
    }

    changed = patch_json(client, f"/api/device-types/{type_id}/", {"rack_height": 0})
    assert (changed.json()["rack_height"], len(changed.json()["interfaces"])) == (0, 2)
    assert client.get(f"/api/device-types/{type_id}/").json()["rack_height"] == 0

    assert client.delete(f"/api/device-types/{type_id}/").status_code == 204
    assert client.get(f"/api/device-types/{type_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)

    assert client.get("/api/device-types/").status_code == 200
    assert post_json(client, "/api/device-types/", NEW_TYPE).status_code == 403
    assert patch_json(client, "/api/device-types/1/", {"make": "Other"}).status_code == 403
    assert client.delete("/api/device-types/1/").status_code == 403


def test_list_is_paginated_and_rejects_unknown_filters(client_with_role):
    client = client_with_role(Role.EDITOR)
    post_json(client, "/api/device-types/", NEW_TYPE)

    assert client.get("/api/device-types/").json()["count"] == 1
    assert client.get("/api/device-types/?make=Acme").status_code == 400


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"interfaces": [{"name": "port[9-1]", "kind": "physical"}]}, "interfaces"),
        ({"interfaces": [{"name": "port1", "kind": "wifi"}]}, "interfaces"),
        ({"interfaces": "port1"}, "interfaces"),
        ({"rack_height": -1}, "rack_height"),
    ],
)
def test_invalid_input_is_a_field_error(client_with_role, change, field):
    client = client_with_role(Role.EDITOR)

    response = post_json(client, "/api/device-types/", {**NEW_TYPE, **change})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert field in response.json()["error"]["fields"]


def test_duplicate_make_and_model_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    post_json(client, "/api/device-types/", NEW_TYPE)

    response = post_json(client, "/api/device-types/", NEW_TYPE)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
