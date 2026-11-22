import pytest

from netatlas.accounts.services import ensure_initial_admin


@pytest.mark.parametrize("path", ["/", "/status/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


def test_login_page_is_public(client):
    response = client.get("/login/")

    assert response.status_code == 200
    assert "<title>Log in · NetAtlas</title>" in response.content.decode()


@pytest.mark.django_db
def test_api_status_stays_public_for_the_healthcheck(client):
    assert client.get("/api/status/").status_code == 200


@pytest.mark.django_db
def test_login_with_valid_credentials_returns_to_the_requested_page(client):
    ensure_initial_admin(username="root", password="correct-horse-battery")

    response = client.post(
        "/login/",
        {"username": "root", "password": "correct-horse-battery", "next": "/status/"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/status/"
    assert client.get("/status/").status_code == 200


@pytest.mark.django_db
def test_login_with_a_wrong_password_is_refused(client):
    ensure_initial_admin(username="root", password="correct-horse-battery")

    response = client.post("/login/", {"username": "root", "password": "wrong"})

    assert response.status_code == 200
    assert client.get("/").status_code == 302


@pytest.mark.django_db
def test_login_does_not_redirect_to_another_site(client):
    ensure_initial_admin(username="root", password="correct-horse-battery")

    response = client.post(
        "/login/",
        {"username": "root", "password": "correct-horse-battery", "next": "https://evil.test/"},
    )

    assert response["Location"] == "/"


def test_logout_ends_the_session(logged_in_client):
    assert 'action="/logout/"' in logged_in_client.get("/").content.decode()

    response = logged_in_client.post("/logout/")

    assert response.status_code == 302
    assert response["Location"] == "/login/"
    assert logged_in_client.get("/").status_code == 302
