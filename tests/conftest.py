import pytest
from django.contrib.auth import authenticate
from django.db import connection

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin


@pytest.fixture
def logged_in_client(client, db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    client.force_login(authenticate(username="root", password="correct-horse-battery"))
    return client


@pytest.fixture
def client_with_role(client, db):
    """A client logged in as a new user with the given Role."""

    def login(role):
        ensure_initial_admin(username="root", password="correct-horse-battery")
        admin = authenticate(username="root", password="correct-horse-battery")
        if role == Role.ADMIN:
            client.force_login(admin)
        else:
            client.force_login(create_user(admin, "member", "another-long-password", role))
        return client

    return login


@pytest.fixture
def unreachable_database(django_db_blocker):
    """Point the real connection at a port where nothing listens."""
    original = connection.settings_dict.copy()
    connection.close()
    connection.settings_dict.update(HOST="127.0.0.1", PORT="1")
    with django_db_blocker.unblock():
        yield
    connection.close()
    connection.settings_dict.clear()
    connection.settings_dict.update(original)
