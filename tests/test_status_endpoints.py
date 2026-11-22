import pytest

from netatlas import __version__
from netatlas.system.services import SystemStatus


def test_api_status_reports_database_down_with_503(client, unreachable_database):
    response = client.get("/api/status/")

    assert response.status_code == 503
    assert response.json() == {"version": __version__, "database": "down"}


def test_status_page_shows_a_database_outage(logged_in_client, monkeypatch):
    down = SystemStatus(version=__version__, database="down")
    monkeypatch.setattr("netatlas.web.views.get_system_status", lambda: down)

    response = logged_in_client.get("/status/")

    assert response.status_code == 200
    html = response.content.decode()
    assert __version__ in html
    assert 'data-database-state="down"' in html


@pytest.mark.parametrize("path", ["/", "/status/", "/users/", "/login/"])
def test_visitor_with_a_session_gets_503_during_a_database_outage(
    client, unreachable_database, path
):
    client.cookies["sessionid"] = "session-key-the-database-cannot-load"

    response = client.get(path)

    assert response.status_code == 503
    html = response.content.decode()
    assert "<title>Unavailable · NetAtlas</title>" in html
    assert 'data-database-state="down"' in html
    assert __version__ in html


def test_status_page_is_linked_from_the_navigation(logged_in_client):
    html = logged_in_client.get("/").content.decode()

    assert 'href="/status/"' in html


@pytest.mark.django_db
def test_api_status_reports_database_up_with_200(client):
    response = client.get("/api/status/")

    assert response.status_code == 200
    assert response.json() == {"version": __version__, "database": "up"}
