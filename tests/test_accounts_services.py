import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError

from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import (
    assign_role,
    create_user,
    ensure_initial_admin,
    get_user,
    list_users,
    role_of,
)


@pytest.mark.django_db
def test_first_start_creates_an_admin_who_can_log_in():
    created = ensure_initial_admin(username="root", password="correct-horse-battery")

    user = authenticate(username="root", password="correct-horse-battery")
    assert created is True
    assert user is not None
    assert role_of(user) == Role.ADMIN


@pytest.mark.django_db
def test_restart_with_an_existing_admin_changes_nothing():
    ensure_initial_admin(username="root", password="correct-horse-battery")

    created = ensure_initial_admin(username="other", password="a-different-password")

    assert created is False
    assert authenticate(username="root", password="correct-horse-battery") is not None
    assert authenticate(username="other", password="a-different-password") is None


@pytest.mark.django_db
@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR, Role.VIEWER])
def test_admin_creates_a_user_with_a_role(role):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")

    create_user(actor=admin, username="alex", password="another-long-password", role=role)

    created = authenticate(username="alex", password="another-long-password")
    assert created is not None
    assert role_of(created) == role


@pytest.mark.django_db
@pytest.mark.parametrize("actor_role", [Role.EDITOR, Role.VIEWER])
def test_only_an_admin_can_create_users(actor_role):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    actor = create_user(admin, "actor", "another-long-password", actor_role)

    with pytest.raises(PermissionDenied):
        create_user(actor, "mallory", "yet-another-password", Role.ADMIN)

    assert authenticate(username="mallory", password="yet-another-password") is None


@pytest.mark.django_db
def test_admin_changes_a_users_role():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    user = create_user(admin, "alex", "another-long-password", Role.VIEWER)

    assign_role(actor=admin, user=user, role=Role.EDITOR)

    assert role_of(authenticate(username="alex", password="another-long-password")) == Role.EDITOR


@pytest.mark.django_db
def test_the_last_admin_cannot_lose_the_admin_role():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")

    with pytest.raises(ValidationError):
        assign_role(actor=admin, user=admin, role=Role.VIEWER)

    assert role_of(authenticate(username="root", password="correct-horse-battery")) == Role.ADMIN


@pytest.mark.django_db
def test_an_admin_can_step_down_while_another_admin_remains():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    create_user(admin, "second", "another-long-password", Role.ADMIN)

    assign_role(actor=admin, user=admin, role=Role.VIEWER)

    assert role_of(authenticate(username="root", password="correct-horse-battery")) == Role.VIEWER


@pytest.mark.django_db
def test_a_non_admin_cannot_change_roles():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    editor = create_user(admin, "editor", "another-long-password", Role.EDITOR)

    with pytest.raises(PermissionDenied):
        assign_role(actor=editor, user=editor, role=Role.ADMIN)

    assert role_of(authenticate(username="editor", password="another-long-password")) == Role.EDITOR


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("username", "password", "role", "field"),
    [
        pytest.param("root", "another-long-password", Role.VIEWER, "username", id="duplicate"),
        pytest.param("", "another-long-password", Role.VIEWER, "username", id="blank-username"),
        pytest.param("has space", "another-long-password", Role.VIEWER, "username", id="bad-chars"),
        pytest.param("alex", "short", Role.VIEWER, "password", id="short-password"),
        pytest.param("alex", "another-long-password", "owner", "role", id="unknown-role"),
    ],
)
def test_invalid_new_user_is_refused(username, password, role, field):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")

    with pytest.raises(ValidationError) as error:
        create_user(admin, username, password, role)

    assert field in error.value.message_dict
    assert [user.username for user in list_users(admin)] == ["root"]


@pytest.mark.django_db
def test_assigning_an_unknown_role_is_refused():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    user = create_user(admin, "alex", "another-long-password", Role.VIEWER)

    with pytest.raises(ValidationError):
        assign_role(admin, user, "owner")

    assert role_of(user) == Role.VIEWER


@pytest.mark.django_db
def test_admin_lists_users_with_their_roles():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    create_user(admin, "alex", "another-long-password", Role.VIEWER)

    listed = [(user.username, user.role) for user in list_users(admin)]

    assert listed == [("alex", Role.VIEWER), ("root", Role.ADMIN)]


@pytest.mark.django_db
def test_a_non_admin_cannot_list_or_fetch_users():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    viewer = create_user(admin, "viewer", "another-long-password", Role.VIEWER)

    with pytest.raises(PermissionDenied):
        list_users(viewer)
    with pytest.raises(PermissionDenied):
        get_user(viewer, admin.pk)


@pytest.mark.django_db
def test_fetching_an_unknown_user_is_not_found():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")

    with pytest.raises(Http404):
        get_user(admin, admin.pk + 1000)
