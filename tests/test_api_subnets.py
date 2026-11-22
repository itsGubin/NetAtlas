import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import create_subnet
from netatlas.places.services import create_site

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/subnets/", "/api/subnets/1/"])
def test_anonymous_requests_are_refused(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", WRITERS)
def test_writer_manages_a_subnet(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")

    created = post_json(client, "/api/subnets/", {"cidr": "10.0.0.0/24", "site_id": site.id})
    assert created.status_code == 201
    subnet_id = created.json()["id"]
    assert created.json() == {
        "id": subnet_id,
        "cidr": "10.0.0.0/24",
        "version": 4,
        "prefix_length": 24,
        "site_id": site.id,
        "site_name": "Home",
        "parent_id": None,
        "parent_cidr": None,
        "depth": 0,
        "utilisation": 0.0,
        "vlan_id": None,
        "vlan_number": None,
        "vlan_name": None,
    }

    changed = patch_json(client, f"/api/subnets/{subnet_id}/", {"cidr": "10.0.1.0/24"})
    assert changed.json()["cidr"] == "10.0.1.0/24"
    assert client.get(f"/api/subnets/{subnet_id}/").json()["cidr"] == "10.0.1.0/24"

    assert client.delete(f"/api/subnets/{subnet_id}/").status_code == 204
    assert client.get(f"/api/subnets/{subnet_id}/").status_code == 404


def test_viewer_can_read_but_not_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    path = f"/api/subnets/{subnet.id}/"

    assert client.get("/api/subnets/").json()["count"] == 1
    assert client.get(path).status_code == 200
    new = {"cidr": "10.0.1.0/24", "site_id": site.id}
    assert post_json(client, "/api/subnets/", new).status_code == 403
    assert patch_json(client, path, {"cidr": "10.0.1.0/24"}).status_code == 403
    assert client.delete(path).status_code == 403


def test_list_is_in_tree_order_with_depth_and_utilisation(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    create_subnet(root(), cidr="10.0.0.128/25", site_id=site.id)
    create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)

    results = client.get("/api/subnets/").json()["results"]

    assert [(s["cidr"], s["depth"], s["utilisation"]) for s in results] == [
        ("10.0.0.0/24", 0, 50.0),
        ("10.0.0.128/25", 1, 0.0),
    ]


def test_subnets_filter_by_site_and_version(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    create_subnet(root(), cidr="10.0.0.0/8", site_id=home.id)
    create_subnet(root(), cidr="2001:db8::/32", site_id=home.id)
    create_subnet(root(), cidr="172.16.0.0/12", site_id=office.id)

    def cidrs(query):
        return [s["cidr"] for s in client.get(f"/api/subnets/?{query}").json()["results"]]

    assert cidrs(f"site={office.id}") == ["172.16.0.0/12"]
    assert cidrs("version=6") == ["2001:db8::/32"]
    for bad in ("site=abc", "version=5", "cidr=10.0.0.0/8"):
        assert client.get(f"/api/subnets/?{bad}").status_code == 400


@pytest.mark.parametrize("cidr", ["nonsense", "10.0.0.1/24", "10.0.0.0/33"])
def test_invalid_cidr_is_a_field_error(client_with_role, cidr):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = post_json(client, "/api/subnets/", {"cidr": cidr, "site_id": site.id})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "cidr" in response.json()["error"]["fields"]


def test_duplicate_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    body = {"cidr": "10.0.0.0/24", "site_id": site.id}
    post_json(client, "/api/subnets/", body)

    response = post_json(client, "/api/subnets/", body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_deleting_a_site_with_a_subnet_is_a_validation_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_site(root(), name="Office")
    create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)

    assert client.delete(f"/api/sites/{site.id}/").status_code == 400
