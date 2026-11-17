import pytest


def test_api_status_reports_database_down_with_503(client, unreachable_database):
    response = client.get("/api/status/")

    assert response.status_code == 503
    assert response.json() == {"version": "0.1.0", "database": "down"}


def test_status_page_shows_the_same_status_as_the_api(client, unreachable_database):
    response = client.get("/status/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "0.1.0" in html
    assert 'data-database-state="down"' in html


def test_status_page_is_linked_from_the_navigation(client):
    html = client.get("/").content.decode()

    assert 'href="/status/"' in html


@pytest.mark.django_db
def test_api_status_reports_database_up_with_200(client):
    response = client.get("/api/status/")

    assert response.status_code == 200
    assert response.json() == {"version": "0.1.0", "database": "up"}
