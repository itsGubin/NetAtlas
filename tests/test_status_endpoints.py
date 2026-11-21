import pytest

from netatlas import __version__


def test_api_status_reports_database_down_with_503(client, unreachable_database):
    response = client.get("/api/status/")

    assert response.status_code == 503
    assert response.json() == {"version": __version__, "database": "down"}


def test_status_page_shows_the_same_status_as_the_api(client, unreachable_database):
    response = client.get("/status/")

    assert response.status_code == 200
    html = response.content.decode()
    assert __version__ in html
    assert 'data-database-state="down"' in html


def test_status_page_is_linked_from_the_navigation(client):
    html = client.get("/").content.decode()

    assert 'href="/status/"' in html


@pytest.mark.django_db
def test_api_status_reports_database_up_with_200(client):
    response = client.get("/api/status/")

    assert response.status_code == 200
    assert response.json() == {"version": __version__, "database": "up"}
