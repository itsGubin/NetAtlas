import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import create_subnet, get_subnet, list_subnets
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/subnets/", "/subnets/1/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_subnet(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")

    response = client.post("/subnets/", {"cidr": "10.0.0.0/24", "site_id": site.id})
    assert response.status_code == 302
    subnet = list_subnets(root())[0]

    client.post(f"/subnets/{subnet.id}/", {"cidr": "10.0.1.0/24", "site_id": site.id})
    assert get_subnet(root(), subnet.id).cidr == "10.0.1.0/24"

    assert confirm_delete(client, f"/subnets/{subnet.id}/delete/").status_code == 302
    assert list_subnets(root()) == []


def test_viewer_sees_subnets_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    new = {"cidr": "10.0.1.0/24", "site_id": site.id}

    for path in ("/subnets/", f"/subnets/{subnet.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count("<form") == 1

    assert client.post("/subnets/", new).status_code == 403
    assert client.post(f"/subnets/{subnet.id}/", new).status_code == 403
    assert client.post(f"/subnets/{subnet.id}/delete/").status_code == 403


def test_list_shows_a_tree_with_utilisation(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    parent = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    child = create_subnet(root(), cidr="10.0.0.128/25", site_id=site.id)

    page = client.get("/subnets/").content.decode()

    assert page.index(f'<a href="/subnets/{parent.id}/">') < page.index(
        f'<a href="/subnets/{child.id}/">'
    )
    assert 'data-depth="0"' in page
    assert 'data-depth="1"' in page
    assert "50.0 %" in page


def test_site_selection_is_hidden_while_only_one_site_exists(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    page = client.get("/subnets/").content.decode()
    assert '<select name="site_id"' not in page
    assert f'type="hidden" name="site_id" value="{site.id}"' in page

    create_site(root(), name="Office")

    assert '<select name="site_id"' in client.get("/subnets/").content.decode()


def test_host_bits_show_the_suggestion_and_keep_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = client.post("/subnets/", {"cidr": "192.168.1.5/24", "site_id": site.id})

    page = response.content.decode()
    assert response.status_code == 400
    assert 'role="alert"' in page
    assert "192.168.1.0/24" in page
    assert 'value="192.168.1.5/24"' in page


def test_invalid_edit_shows_the_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)

    response = client.post(f"/subnets/{subnet.id}/", {"cidr": "nope", "site_id": site.id})

    assert response.status_code == 400
    assert 'value="nope"' in response.content.decode()
    assert get_subnet(root(), subnet.id).cidr == "10.0.0.0/24"


@pytest.mark.parametrize("site_id", ["", "abc", "999999"])
def test_invalid_site_is_a_form_error(client_with_role, site_id):
    client = client_with_role(Role.EDITOR)
    create_site(root(), name="Home")

    response = client.post("/subnets/", {"cidr": "10.0.0.0/24", "site_id": site_id})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_item_view_shows_parent_children_and_history(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    parent = create_subnet(root(), cidr="10.0.0.0/16", site_id=site.id)
    middle = create_subnet(root(), cidr="10.0.1.0/24", site_id=site.id)
    child = create_subnet(root(), cidr="10.0.1.0/25", site_id=site.id)

    page = client.get(f"/subnets/{middle.id}/").content.decode()

    assert f'<a href="/subnets/{parent.id}/">10.0.0.0/16</a>' in page
    assert f'<a href="/subnets/{child.id}/">10.0.1.0/25</a>' in page
    assert 'data-history-action="created"' in page


def test_navigation_links_to_subnets(client_with_role):
    assert 'href="/subnets/"' in client_with_role(Role.VIEWER).get("/").content.decode()
