import pytest
from django.contrib.auth import authenticate
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, ensure_initial_admin
from netatlas.history.services import create_item, delete_item, update_item
from tests.history_items.models import Widget

ROLES = [Role.ADMIN, Role.EDITOR, Role.VIEWER]


@pytest.fixture
def admin(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def widget(admin):
    item = create_item(admin, Widget, name="alpha", size=3)
    return update_item(admin, item, name="beta")


def test_web_history_requires_login(client, widget):
    path = f"/history/widget/{widget.pk}/"

    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", ROLES)
def test_every_role_sees_the_history_page(client_with_role, role):
    client = client_with_role(role)
    admin = authenticate(username="root", password="correct-horse-battery")
    item = create_item(admin, Widget, name="alpha")
    update_item(admin, item, name="beta")

    response = client.get(f"/history/widget/{item.pk}/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "<title>History of beta · NetAtlas</title>" in html
    assert 'data-history-action="updated"' in html
    assert 'data-history-action="created"' in html
    assert "root" in html
    assert "<td>name</td>" in html
    assert "<td>alpha</td>" in html
    assert "<td>beta</td>" in html


def test_the_page_shows_newest_first(logged_in_client, admin, widget):
    html = logged_in_client.get(f"/history/widget/{widget.pk}/").content.decode()

    assert html.index('data-history-action="updated"') < html.index(
        'data-history-action="created"'
    )


def test_history_values_are_escaped(logged_in_client, admin):
    item = create_item(admin, Widget, name="safe", notes="<script>alert(1)</script>")

    html = logged_in_client.get(f"/history/widget/{item.pk}/").content.decode()

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_deleted_items_history_stays_visible(logged_in_client, admin, widget):
    widget_id = widget.pk
    delete_item(admin, widget)

    response = logged_in_client.get(f"/history/widget/{widget_id}/")

    assert response.status_code == 200
    assert 'data-history-action="deleted"' in response.content.decode()


def test_web_history_of_an_unknown_item_is_not_found(logged_in_client):
    assert logged_in_client.get("/history/widget/999999/").status_code == 404


def test_api_history_requires_authentication(widget):
    assert Client().get(f"/api/history/widget/{widget.pk}/").status_code == 401


@pytest.mark.parametrize("role", ROLES)
def test_every_role_reads_history_through_the_api(client_with_role, role):
    client = client_with_role(role)
    admin = authenticate(username="root", password="correct-horse-battery")
    item = create_item(admin, Widget, name="alpha")
    update_item(admin, item, name="beta")

    response = client.get(f"/api/history/widget/{item.pk}/")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    latest = body["results"][0]
    assert latest["action"] == "updated"
    assert latest["actor"] == "root"
    assert latest["item_kind"] == "widget"
    assert latest["item_id"] == item.pk
    assert latest["item_label"] == "beta"
    assert latest["at"]
    assert latest["changes"] == [{"field": "name", "old": "alpha", "new": "beta"}]


def test_api_history_works_with_a_token_and_pagination(admin, widget):
    client = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(admin, 'script').secret}")

    body = client.get(f"/api/history/widget/{widget.pk}/?page_size=1").json()

    assert body["count"] == 2
    assert len(body["results"]) == 1
    assert "page=2" in body["next"]


def test_api_history_of_an_unknown_item_is_not_found(logged_in_client):
    response = logged_in_client.get("/api/history/widget/999999/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_api_history_refuses_unknown_filters(logged_in_client, widget):
    response = logged_in_client.get(f"/api/history/widget/{widget.pk}/?actor=root")

    assert response.status_code == 400
    assert "actor" in response.json()["error"]["fields"]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_api_history_cannot_be_rewritten(logged_in_client, widget, method):
    path = f"/api/history/widget/{widget.pk}/"

    response = getattr(logged_in_client, method)(path, {}, content_type="application/json")

    assert response.status_code == 405
    assert logged_in_client.get(path).json()["count"] == 2


def test_history_is_in_the_schema(logged_in_client):
    schema = logged_in_client.get("/api/schema/").json()

    assert "/api/history/{kind}/{item_id}/" in schema["paths"]
