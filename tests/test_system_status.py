import pytest

from netatlas import __version__
from netatlas.system.services import get_system_status


def test_status_reports_the_running_version_even_when_the_database_is_down(unreachable_database):
    assert get_system_status().version == __version__


def test_status_reports_database_down_when_it_cannot_be_reached(unreachable_database):
    assert get_system_status().database == "down"


@pytest.mark.django_db
def test_status_reports_database_up_when_it_is_reachable():
    assert get_system_status().database == "up"
