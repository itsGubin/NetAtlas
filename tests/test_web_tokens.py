from datetime import timedelta

import pytest
from django.contrib.auth import authenticate
from django.utils import timezone

from netatlas.accounts.roles import Role
from netatlas.accounts.services import (
    authenticate_token,
    create_token,
    create_user,
    ensure_initial_admin,
    list_tokens,
)


def secret_in(html):
    start = html.index('data-token-secret="') + len('data-token-secret="')
    return html[start : html.index('"', start)]


def test_anonymous_visitor_is_sent_to_login(client, db):
    response = client.get("/tokens/")

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=/tokens/"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR, Role.VIEWER])
def test_every_role_finds_tokens_in_the_navigation(client_with_role, role):
    assert 'href="/tokens/"' in client_with_role(role).get("/").content.decode()


@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR, Role.VIEWER])
def test_a_user_creates_a_token_and_sees_the_secret_once(client_with_role, role):
    client = client_with_role(role)

    created = client.post("/tokens/", {"name": "backup script"})

    assert created.status_code == 201
    assert "no-store" in created["Cache-Control"]
    secret = secret_in(created.content.decode())
    assert authenticate_token(secret) is not None
    page = client.get("/tokens/").content.decode()
    assert "backup script" in page
    assert secret not in page


def test_a_token_can_expire(client_with_role):
    client = client_with_role(Role.VIEWER)
    expiry = (timezone.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")

    response = client.post("/tokens/", {"name": "temporary", "expires_at": expiry})

    assert response.status_code == 201
    assert authenticate_token(secret_in(response.content.decode())) is not None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"name": ""}, "This field cannot be blank"),
        ({"name": "x", "expires_at": "not-a-date"}, "valid date"),
        ({"name": "x", "expires_at": "2000-01-01T00:00"}, "in the future"),
    ],
)
def test_invalid_token_shows_the_error(client_with_role, data, message):
    client = client_with_role(Role.VIEWER)

    response = client.post("/tokens/", data)

    assert response.status_code == 400
    assert message in response.content.decode()
    assert "data-token-secret" not in response.content.decode()


def test_a_user_sees_only_their_own_tokens(client_with_role):
    client = client_with_role(Role.VIEWER)
    admin = authenticate(username="root", password="correct-horse-battery")
    create_token(admin, "admin-only-token")

    assert "admin-only-token" not in client.get("/tokens/").content.decode()


def test_admin_sees_and_revokes_every_token(client_with_role):
    client = client_with_role(Role.ADMIN)
    admin = authenticate(username="root", password="correct-horse-battery")
    member = create_user(admin, "alex", "another-long-password", Role.VIEWER)
    token = create_token(member, "alex-token")

    assert "alex-token" in client.get("/tokens/").content.decode()
    response = client.post(f"/tokens/{token.summary.id}/revoke/")

    assert response.status_code == 302
    assert authenticate_token(token.secret) is None


def test_a_user_revokes_their_own_token(client_with_role):
    client = client_with_role(Role.VIEWER)
    secret = secret_in(client.post("/tokens/", {"name": "old"}).content.decode())
    user = authenticate_token(secret)
    token_id = list_tokens(user)[0].id

    assert client.post(f"/tokens/{token_id}/revoke/").status_code == 302
    assert authenticate_token(secret) is None


def test_a_user_cannot_revoke_another_users_token(client_with_role):
    client = client_with_role(Role.EDITOR)
    admin = authenticate(username="root", password="correct-horse-battery")
    token = create_token(admin, "admin token")

    assert client.post(f"/tokens/{token.summary.id}/revoke/").status_code == 404
    assert authenticate_token(token.secret) == admin


def test_revoking_only_accepts_post(client_with_role):
    client = client_with_role(Role.ADMIN)
    admin = authenticate(username="root", password="correct-horse-battery")
    token = create_token(admin, "script")

    assert client.get(f"/tokens/{token.summary.id}/revoke/").status_code == 405
    assert authenticate_token(token.secret) == admin


def test_writes_require_a_csrf_token(db):
    from django.test import Client

    ensure_initial_admin(username="root", password="correct-horse-battery")
    client = Client(enforce_csrf_checks=True)
    client.force_login(authenticate(username="root", password="correct-horse-battery"))

    assert client.post("/tokens/", {"name": "script"}).status_code == 403
