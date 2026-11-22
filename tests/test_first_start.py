import pytest
from django.contrib.auth import authenticate
from django.core.management import CommandError, call_command

from netatlas.accounts.roles import Role
from netatlas.accounts.services import ensure_initial_admin, role_of

COMMAND = "ensure_initial_admin"


@pytest.mark.django_db
def test_first_start_creates_the_admin_from_the_environment(monkeypatch):
    monkeypatch.setenv("NETATLAS_ADMIN_USERNAME", "root")
    monkeypatch.setenv("NETATLAS_ADMIN_PASSWORD", "correct-horse-battery")

    call_command(COMMAND)

    assert role_of(authenticate(username="root", password="correct-horse-battery")) == Role.ADMIN


@pytest.mark.django_db
@pytest.mark.parametrize(
    "missing", ["NETATLAS_ADMIN_USERNAME", "NETATLAS_ADMIN_PASSWORD"]
)
def test_first_start_without_credentials_stops_with_a_clear_error(monkeypatch, missing):
    monkeypatch.setenv("NETATLAS_ADMIN_USERNAME", "root")
    monkeypatch.setenv("NETATLAS_ADMIN_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv(missing, "")

    with pytest.raises(CommandError, match=missing):
        call_command(COMMAND)


@pytest.mark.django_db
def test_first_start_with_a_weak_password_stops_with_a_clear_error(monkeypatch):
    monkeypatch.setenv("NETATLAS_ADMIN_USERNAME", "root")
    monkeypatch.setenv("NETATLAS_ADMIN_PASSWORD", "short")

    with pytest.raises(CommandError, match="Invalid initial Admin"):
        call_command(COMMAND)


@pytest.mark.django_db
def test_later_starts_need_no_credentials(monkeypatch):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    monkeypatch.delenv("NETATLAS_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("NETATLAS_ADMIN_PASSWORD", raising=False)

    call_command(COMMAND)

    assert authenticate(username="root", password="correct-horse-battery") is not None
