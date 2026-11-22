import pytest

from netatlas.accounts.roles import Role

WRITERS = [Role.ADMIN, Role.EDITOR]


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/customers/", "/api/sites/"])
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_customers(client_with_role, role):
    client = client_with_role(role)

    created = post_json(client, "/api/customers/", {"name": "Acme"})
    assert created.status_code == 201
    customer_id = created.json()["id"]

    renamed = patch_json(client, f"/api/customers/{customer_id}/", {"name": "Acme AG"})
    assert renamed.json() == {"id": customer_id, "name": "Acme AG"}
    assert client.get(f"/api/customers/{customer_id}/").json()["name"] == "Acme AG"

    assert client.delete(f"/api/customers/{customer_id}/").status_code == 204
    assert client.get(f"/api/customers/{customer_id}/").status_code == 404


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_sites(client_with_role, role):
    client = client_with_role(role)
    customer_id = post_json(client, "/api/customers/", {"name": "Acme"}).json()["id"]
    post_json(client, "/api/sites/", {"name": "Home"})

    created = post_json(client, "/api/sites/", {"name": "Office", "customer_id": customer_id})
    assert created.status_code == 201
    site_id = created.json()["id"]
    assert created.json() == {
        "id": site_id,
        "name": "Office",
        "customer_id": customer_id,
        "customer_name": "Acme",
    }

    moved = patch_json(client, f"/api/sites/{site_id}/", {"customer_id": None})
    assert (moved.json()["name"], moved.json()["customer_id"]) == ("Office", None)

    assert client.delete(f"/api/sites/{site_id}/").status_code == 204
    assert client.get(f"/api/sites/{site_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)

    assert client.get("/api/customers/").status_code == 200
    assert client.get("/api/sites/").status_code == 200
    assert post_json(client, "/api/customers/", {"name": "Acme"}).status_code == 403
    assert post_json(client, "/api/sites/", {"name": "Office"}).status_code == 403
    assert patch_json(client, "/api/sites/1/", {"name": "Office"}).status_code == 403
    assert client.delete("/api/customers/1/").status_code == 403
    assert client.delete("/api/sites/1/").status_code == 403


def test_sites_filter_by_customer(client_with_role):
    client = client_with_role(Role.EDITOR)
    customer_id = post_json(client, "/api/customers/", {"name": "Acme"}).json()["id"]
    post_json(client, "/api/sites/", {"name": "Home"})
    post_json(client, "/api/sites/", {"name": "Office", "customer_id": customer_id})

    response = client.get(f"/api/sites/?customer={customer_id}")

    assert [site["name"] for site in response.json()["results"]] == ["Office"]
    assert client.get("/api/sites/?customer=abc").status_code == 400
    assert client.get("/api/sites/?custmer=1").status_code == 400


def test_deleting_the_last_site_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site_id = post_json(client, "/api/sites/", {"name": "Home"}).json()["id"]

    response = client.delete(f"/api/sites/{site_id}/")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert client.get(f"/api/sites/{site_id}/").status_code == 200


def test_site_with_an_unknown_customer_is_refused(client_with_role):
    client = client_with_role(Role.EDITOR)

    response = post_json(client, "/api/sites/", {"name": "Office", "customer_id": 999999})

    assert response.status_code == 400
    assert "customer" in response.json()["error"]["fields"]
