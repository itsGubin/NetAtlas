import pytest
from django.contrib.auth import authenticate
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import ensure_initial_admin, list_users, role_of

NON_ADMINS = [Role.EDITOR, Role.VIEWER]
NEW_USER = {"username": "alex", "password": "another-long-password", "role": "viewer"}


def user_id(client, username):
    admin = authenticate(username="root", password="correct-horse-battery")
    return next(user.id for user in list_users(admin) if user.username == username)


@pytest.mark.django_db
def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/users/")

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=/users/"


@pytest.mark.parametrize("role", NON_ADMINS)
def test_non_admins_cannot_use_user_management(client_with_role, role):
    client = client_with_role(role)
    root_id = user_id(client, "root")

    assert client.get("/users/").status_code == 403
    assert client.post("/users/", NEW_USER).status_code == 403
    assert client.post(f"/users/{root_id}/role/", {"role": "viewer"}).status_code == 403
    assert authenticate(username="alex", password="another-long-password") is None
    assert role_of(authenticate(username="root", password="correct-horse-battery")) == Role.ADMIN


@pytest.mark.parametrize("role", NON_ADMINS)
def test_navigation_hides_users_from_non_admins(client_with_role, role):
    assert 'href="/users/"' not in client_with_role(role).get("/").content.decode()


def test_admin_sees_users_in_navigation_and_list(client_with_role):
    client = client_with_role(Role.ADMIN)

    assert 'href="/users/"' in client.get("/").content.decode()
    response = client.get("/users/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "<title>Users · NetAtlas</title>" in html
    assert "root" in html


def test_admin_creates_a_user(client_with_role):
    client = client_with_role(Role.ADMIN)

    response = client.post("/users/", NEW_USER)

    assert response.status_code == 302
    assert response["Location"] == "/users/"
    assert role_of(authenticate(username="alex", password="another-long-password")) == Role.VIEWER


def test_invalid_new_user_shows_the_error_and_keeps_the_username(client_with_role):
    client = client_with_role(Role.ADMIN)

    response = client.post("/users/", {**NEW_USER, "password": "short"})

    assert response.status_code == 400
    html = response.content.decode()
    assert "too short" in html
    assert 'value="alex"' in html
    assert "short" not in html.split('name="password"')[1].split(">")[0]


def test_admin_changes_a_role(client_with_role):
    client = client_with_role(Role.ADMIN)
    client.post("/users/", NEW_USER)

    response = client.post(f"/users/{user_id(client, 'alex')}/role/", {"role": "editor"})

    assert response.status_code == 302
    assert role_of(authenticate(username="alex", password="another-long-password")) == Role.EDITOR


def test_last_admin_cannot_be_demoted(client_with_role):
    client = client_with_role(Role.ADMIN)

    response = client.post(f"/users/{user_id(client, 'root')}/role/", {"role": "viewer"})

    assert response.status_code == 400
    assert "at least one Admin" in response.content.decode()
    assert role_of(authenticate(username="root", password="correct-horse-battery")) == Role.ADMIN


def test_changing_an_unknown_user_is_not_found(client_with_role):
    client = client_with_role(Role.ADMIN)

    assert client.post("/users/999999/role/", {"role": "editor"}).status_code == 404


def test_role_change_only_accepts_post(client_with_role):
    client = client_with_role(Role.ADMIN)

    assert client.get(f"/users/{user_id(client, 'root')}/role/").status_code == 405


def test_web_writes_require_a_csrf_token(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    client = Client(enforce_csrf_checks=True)
    client.force_login(authenticate(username="root", password="correct-horse-battery"))

    assert client.post("/users/", NEW_USER).status_code == 403
    assert authenticate(username="alex", password="another-long-password") is None
