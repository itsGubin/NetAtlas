from datetime import timedelta

import pytest
from django.contrib.auth import authenticate
from django.test import Client
from django.utils import timezone

from netatlas.accounts.roles import Role
from netatlas.accounts.services import (
    authenticate_token,
    create_token,
    create_user,
    ensure_initial_admin,
    revoke_token,
)


@pytest.fixture
def admin(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def member(admin):
    return create_user(admin, "member", "another-long-password", Role.VIEWER)


def bearer(secret):
    return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}


def test_anonymous_request_is_told_how_to_authenticate(db):
    response = Client().get("/api/users/")

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"


def test_a_token_authenticates_with_its_owners_role(admin, member):
    admin_token = create_token(admin, "admin script")
    member_token = create_token(member, "member script")

    assert Client().get("/api/users/", **bearer(admin_token.secret)).status_code == 200
    assert Client().get("/api/users/", **bearer(member_token.secret)).status_code == 403


@pytest.mark.parametrize(
    "header",
    ["Bearer nat_not-a-real-token", "Bearer", "Bearer two parts"],
)
def test_a_bad_token_is_refused(db, header):
    response = Client().get("/api/users/", HTTP_AUTHORIZATION=header)

    assert response.status_code == 401


def test_a_revoked_token_is_refused(admin):
    token = create_token(admin, "old laptop")
    revoke_token(admin, token.summary.id)

    assert Client().get("/api/users/", **bearer(token.secret)).status_code == 401


def test_token_writes_need_no_csrf_token(admin):
    token = create_token(admin, "script")
    client = Client(enforce_csrf_checks=True)

    response = client.post(
        "/api/users/",
        {"username": "alex", "password": "another-long-password", "role": "viewer"},
        content_type="application/json",
        **bearer(token.secret),
    )

    assert response.status_code == 201


def test_a_logged_in_user_creates_a_token_and_sees_the_secret_once(admin):
    client = Client()
    client.force_login(admin)

    created = client.post(
        "/api/tokens/", {"name": "backup script"}, content_type="application/json"
    )

    assert created.status_code == 201
    secret = created.json()["secret"]
    assert authenticate_token(secret) == admin
    listed = client.get("/api/tokens/").json()["results"]
    assert [token["name"] for token in listed] == ["backup script"]
    assert "secret" not in listed[0]
    assert secret not in client.get("/api/tokens/").content.decode()


def test_a_token_cannot_mint_another_token(admin):
    token = create_token(admin, "leaked")

    response = Client().post(
        "/api/tokens/",
        {"name": "persistence"},
        content_type="application/json",
        **bearer(token.secret),
    )

    assert response.status_code == 403


def test_a_token_can_be_created_with_an_expiry(admin):
    client = Client()
    client.force_login(admin)
    expires_at = (timezone.now() + timedelta(days=30)).isoformat()

    response = client.post(
        "/api/tokens/",
        {"name": "temporary", "expires_at": expires_at},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["expires_at"] is not None


def test_users_list_only_their_own_tokens(admin, member):
    create_token(admin, "admin token")
    own = create_token(member, "member token")

    response = Client().get("/api/tokens/", **bearer(own.secret))

    assert [token["name"] for token in response.json()["results"]] == ["member token"]


def test_admin_lists_every_token(admin, member):
    token = create_token(admin, "admin token")
    create_token(member, "member token")

    response = Client().get("/api/tokens/?scope=all", **bearer(token.secret))

    assert {t["username"] for t in response.json()["results"]} == {"root", "member"}


def test_a_non_admin_cannot_list_every_token(member):
    token = create_token(member, "member token")

    response = Client().get("/api/tokens/?scope=all", **bearer(token.secret))

    assert response.status_code == 403


def test_a_user_revokes_their_own_token(member):
    token = create_token(member, "old laptop")
    other = create_token(member, "still in use")

    response = Client().delete(f"/api/tokens/{token.summary.id}/", **bearer(other.secret))

    assert response.status_code == 204
    assert authenticate_token(token.secret) is None
    assert authenticate_token(other.secret) == member


def test_admin_revokes_another_users_token(admin, member):
    admin_token = create_token(admin, "admin token")
    token = create_token(member, "member token")

    response = Client().delete(f"/api/tokens/{token.summary.id}/", **bearer(admin_token.secret))

    assert response.status_code == 204
    assert authenticate_token(token.secret) is None


def test_another_users_token_looks_like_it_does_not_exist(admin, member):
    admin_token = create_token(admin, "admin token")
    member_token = create_token(member, "member token")

    response = Client().delete(
        f"/api/tokens/{admin_token.summary.id}/", **bearer(member_token.secret)
    )

    assert response.status_code == 404
    assert authenticate_token(admin_token.secret) == admin
