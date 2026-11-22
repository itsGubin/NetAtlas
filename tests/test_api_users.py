import pytest
from django.contrib.auth import authenticate
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import role_of

NON_ADMINS = [Role.EDITOR, Role.VIEWER]
NEW_USER = {"username": "alex", "password": "another-long-password", "role": "viewer"}


def post_json(client, path, data):
    return client.post(path, data, content_type="application/json")


def patch_json(client, path, data):
    return client.patch(path, data, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/users/"), ("post", "/api/users/"), ("patch", "/api/users/1/")],
)
def test_anonymous_requests_are_refused_without_a_redirect(client, method, path):
    response = getattr(client, method)(path, {}, content_type="application/json")

    assert response.status_code == 401


@pytest.mark.parametrize("role", NON_ADMINS)
def test_non_admins_cannot_use_user_management(client_with_role, role):
    client = client_with_role(role)

    assert client.get("/api/users/").status_code == 403
    assert post_json(client, "/api/users/", NEW_USER).status_code == 403
    assert patch_json(client, "/api/users/1/", {"role": "admin"}).status_code == 403
    assert authenticate(username="alex", password="another-long-password") is None


def test_admin_lists_users_without_exposing_passwords(client_with_role):
    client = client_with_role(Role.ADMIN)

    response = client.get("/api/users/")

    assert response.status_code == 200
    assert [(u["username"], u["role"]) for u in response.json()["results"]] == [("root", "admin")]
    assert set(response.json()["results"][0]) == {"id", "username", "role"}


def test_admin_creates_a_user(client_with_role):
    client = client_with_role(Role.ADMIN)

    response = post_json(client, "/api/users/", NEW_USER)

    assert response.status_code == 201
    assert response.json()["username"] == "alex"
    assert response.json()["role"] == "viewer"
    assert "password" not in response.json()
    assert role_of(authenticate(username="alex", password="another-long-password")) == Role.VIEWER


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"username": "root"}, "username"),
        ({"password": "short"}, "password"),
        ({"role": "owner"}, "role"),
    ],
)
def test_invalid_new_user_gets_field_errors(client_with_role, change, field):
    client = client_with_role(Role.ADMIN)

    response = post_json(client, "/api/users/", {**NEW_USER, **change})

    assert response.status_code == 400
    assert field in response.json()["error"]["fields"]


@pytest.mark.parametrize("missing", ["username", "password", "role"])
def test_incomplete_new_user_gets_a_field_error(client_with_role, missing):
    client = client_with_role(Role.ADMIN)
    body = {key: value for key, value in NEW_USER.items() if key != missing}

    response = post_json(client, "/api/users/", body)

    assert response.status_code == 400
    assert missing in response.json()["error"]["fields"]


def test_admin_changes_a_role(client_with_role):
    client = client_with_role(Role.ADMIN)
    user_id = post_json(client, "/api/users/", NEW_USER).json()["id"]

    response = patch_json(client, f"/api/users/{user_id}/", {"role": "editor"})

    assert response.status_code == 200
    assert response.json()["role"] == "editor"
    assert role_of(authenticate(username="alex", password="another-long-password")) == Role.EDITOR


def test_last_admin_cannot_be_demoted(client_with_role):
    client = client_with_role(Role.ADMIN)
    root_id = client.get("/api/users/").json()["results"][0]["id"]

    response = patch_json(client, f"/api/users/{root_id}/", {"role": "viewer"})

    assert response.status_code == 400
    assert "role" in response.json()["error"]["fields"]


def test_changing_an_unknown_user_is_not_found(client_with_role):
    client = client_with_role(Role.ADMIN)

    assert patch_json(client, "/api/users/999999/", {"role": "editor"}).status_code == 404


def test_session_writes_require_a_csrf_token(db):
    from netatlas.accounts.services import ensure_initial_admin

    ensure_initial_admin(username="root", password="correct-horse-battery")
    client = Client(enforce_csrf_checks=True)
    client.force_login(authenticate(username="root", password="correct-horse-battery"))

    assert post_json(client, "/api/users/", NEW_USER).status_code == 403
    assert authenticate(username="alex", password="another-long-password") is None
