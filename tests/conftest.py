import pytest
from django.db import connection


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
