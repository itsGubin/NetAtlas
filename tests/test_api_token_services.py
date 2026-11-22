from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import Http404
from django.utils import timezone

from netatlas.accounts.roles import Role
from netatlas.accounts.services import (
    authenticate_token,
    create_token,
    create_user,
    ensure_initial_admin,
    list_all_tokens,
    list_tokens,
    revoke_token,
)


def time_machine_at(moment):
    return mock.patch("django.utils.timezone.now", return_value=moment)


@pytest.fixture
def admin(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


def test_a_new_token_authenticates_its_owner(admin):
    token = create_token(actor=admin, name="backup script")

    assert authenticate_token(token.secret) == admin
    assert token.summary.name == "backup script"


def test_an_unknown_token_authenticates_nobody(admin):
    create_token(actor=admin, name="backup script")

    assert authenticate_token("nat_not-a-real-token") is None


def test_the_secret_is_not_stored(admin):
    token = create_token(actor=admin, name="backup script")

    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM accounts_apitoken")
        stored = " ".join(str(value) for row in cursor.fetchall() for value in row)
    assert token.secret not in stored


def test_an_expired_token_authenticates_nobody(admin):
    token = create_token(admin, "short lived", expires_at=timezone.now() + timedelta(hours=1))

    with time_machine_at(timezone.now() + timedelta(hours=2)):
        assert authenticate_token(token.secret) is None


def test_a_token_works_until_it_expires(admin):
    token = create_token(admin, "short lived", expires_at=timezone.now() + timedelta(hours=1))

    assert authenticate_token(token.secret) == admin


def test_a_revoked_token_authenticates_nobody(admin):
    token = create_token(admin, "old laptop")

    revoke_token(admin, token.summary.id)

    assert authenticate_token(token.secret) is None


def test_a_deactivated_users_token_authenticates_nobody(admin):
    member = create_user(admin, "member", "another-long-password", Role.EDITOR)
    token = create_token(member, "script")
    member.is_active = False
    member.save()

    assert authenticate_token(token.secret) is None


def test_users_see_only_their_own_tokens(admin):
    member = create_user(admin, "member", "another-long-password", Role.VIEWER)
    create_token(admin, "admin token")
    create_token(member, "member token")

    assert [token.name for token in list_tokens(member)] == ["member token"]


def test_admin_sees_every_token(admin):
    member = create_user(admin, "member", "another-long-password", Role.VIEWER)
    create_token(admin, "admin token")
    create_token(member, "member token")

    listed = {(token.username, token.name) for token in list_all_tokens(admin)}

    assert listed == {("root", "admin token"), ("member", "member token")}


@pytest.mark.parametrize("role", [Role.EDITOR, Role.VIEWER])
def test_a_non_admin_cannot_see_every_token(admin, role):
    member = create_user(admin, "member", "another-long-password", role)

    with pytest.raises(PermissionDenied):
        list_all_tokens(member)


def test_admin_can_revoke_another_users_token(admin):
    member = create_user(admin, "member", "another-long-password", Role.VIEWER)
    token = create_token(member, "member token")

    revoke_token(admin, token.summary.id)

    assert authenticate_token(token.secret) is None


def test_a_user_cannot_revoke_or_detect_another_users_token(admin):
    member = create_user(admin, "member", "another-long-password", Role.EDITOR)
    token = create_token(admin, "admin token")

    with pytest.raises(Http404):
        revoke_token(member, token.summary.id)

    assert authenticate_token(token.secret) == admin


def test_a_user_without_a_role_cannot_create_a_token(admin):
    roleless = get_user_model().objects.create_user("roleless", password="another-long-password")

    with pytest.raises(PermissionDenied):
        create_token(roleless, "script")


@pytest.mark.parametrize(
    ("name", "expires_in", "field"),
    [
        pytest.param("", None, "name", id="blank-name"),
        pytest.param("x" * 101, None, "name", id="long-name"),
        pytest.param("script", timedelta(hours=-1), "expires_at", id="already-expired"),
    ],
)
def test_invalid_token_is_refused(admin, name, expires_in, field):
    expires_at = timezone.now() + expires_in if expires_in else None

    with pytest.raises(ValidationError) as error:
        create_token(admin, name, expires_at=expires_at)

    assert field in error.value.message_dict
    assert list_tokens(admin) == []
