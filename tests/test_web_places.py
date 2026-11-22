import pytest
from django.contrib.auth import authenticate
from django.template.loader import render_to_string

from netatlas.accounts.roles import Role
from netatlas.places.services import create_customer, create_site, list_customers, list_sites

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/sites/", "/customers/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_customer(client_with_role, role):
    client = client_with_role(role)

    assert client.post("/customers/", {"name": "Acme"}).status_code == 302
    customer = list_customers(root())[0]

    client.post(f"/customers/{customer.id}/", {"name": "Acme AG"})
    assert "Acme AG" in client.get(f"/customers/{customer.id}/").content.decode()

    assert client.post(f"/customers/{customer.id}/delete/").status_code == 302
    assert list_customers(root()) == []


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_site(client_with_role, role):
    client = client_with_role(role)
    create_site(root(), name="Home")
    customer = create_customer(root(), name="Acme")

    client.post("/sites/", {"name": "Office", "customer_id": customer.id})
    site = next(s for s in list_sites(root()) if s.name == "Office")
    assert site.customer_id == customer.id

    client.post(f"/sites/{site.id}/", {"name": "Branch", "customer_id": ""})
    edited = next(s for s in list_sites(root()) if s.id == site.id)
    assert (edited.name, edited.customer_id) == ("Branch", None)

    assert client.post(f"/sites/{site.id}/delete/").status_code == 302
    assert [s.name for s in list_sites(root())] == ["Home"]


def test_viewer_sees_items_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    customer = create_customer(root(), name="Acme")

    for path in ("/sites/", f"/sites/{site.id}/", "/customers/", f"/customers/{customer.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'method="post" action="/logout/"' in response.content.decode()
        assert response.content.decode().count("<form") == 1

    assert client.post("/sites/", {"name": "Office"}).status_code == 403
    assert client.post(f"/sites/{site.id}/", {"name": "Office"}).status_code == 403
    assert client.post(f"/sites/{site.id}/delete/").status_code == 403
    assert client.post("/customers/", {"name": "Other"}).status_code == 403
    assert client.post(f"/customers/{customer.id}/", {"name": "Other"}).status_code == 403
    assert client.post(f"/customers/{customer.id}/delete/").status_code == 403


def test_deleting_the_last_site_shows_why(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = client.post(f"/sites/{site.id}/delete/")

    assert response.status_code == 400
    assert "at least one Site" in response.content.decode()
    assert [s.name for s in list_sites(root())] == ["Home"]


def test_invalid_site_shows_the_error_and_keeps_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)

    response = client.post("/sites/", {"name": "x" * 101})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert "x" * 101 in response.content.decode()


def test_customers_stay_hidden_until_one_exists(client_with_role):
    client = client_with_role(Role.EDITOR)
    create_site(root(), name="Home")

    page = client.get("/sites/").content.decode()
    assert '<a href="/customers/">Customers</a>' not in page
    assert 'name="customer_id"' not in page

    create_customer(root(), name="Acme")

    page = client.get("/sites/").content.decode()
    assert '<a href="/customers/">Customers</a>' in page
    assert 'name="customer_id"' in page


def test_editor_without_customers_can_still_reach_the_first_one(client_with_role):
    client = client_with_role(Role.EDITOR)

    assert 'href="/customers/"' in client.get("/sites/").content.decode()


def test_site_selection_is_hidden_while_only_one_site_exists(client_with_role):
    client_with_role(Role.EDITOR)
    only = create_site(root(), name="Home")

    html = render_to_string("netatlas/_site_select.html", {"sites": list_sites(root())})
    assert "<select" not in html
    assert f'type="hidden" name="site_id" value="{only.id}"' in html

    create_site(root(), name="Office")

    html = render_to_string("netatlas/_site_select.html", {"sites": list_sites(root())})
    assert "<select" in html


def test_invalid_site_keeps_the_chosen_customer(client_with_role):
    client = client_with_role(Role.EDITOR)
    customer = create_customer(root(), name="Acme")

    response = client.post("/sites/", {"name": "x" * 101, "customer_id": customer.id})

    assert f'<option value="{customer.id}" selected>' in response.content.decode()


@pytest.mark.parametrize("kind", ["site", "customer"])
def test_item_view_shows_its_history(client_with_role, kind):
    client = client_with_role(Role.VIEWER)
    item = (create_site if kind == "site" else create_customer)(root(), name="Tracked")

    page = client.get(f"/{kind}s/{item.id}/").content.decode()

    assert 'data-history-action="created"' in page
    assert "root" in page
