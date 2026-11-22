import pytest
from django.contrib.auth import authenticate
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin


@pytest.fixture
def admin(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def api(admin):
    return Client(HTTP_AUTHORIZATION=f"Bearer {create_token(admin, 'tests').secret}")


def test_lists_are_paginated(api, admin):
    for number in range(3):
        create_user(admin, f"user{number}", "another-long-password", Role.VIEWER)

    response = api.get("/api/users/?page_size=2")

    body = response.json()
    assert response.status_code == 200
    assert body["count"] == 4
    assert len(body["results"]) == 2
    assert "page=2" in body["next"]
    assert body["previous"] is None


def test_page_size_is_capped(api, admin):
    for number in range(3):
        create_user(admin, f"user{number}", "another-long-password", Role.VIEWER)

    for number in range(3, 203):
        create_user(admin, f"user{number}", "another-long-password", Role.VIEWER)

    body = api.get("/api/users/?page_size=100000").json()

    assert body["count"] == 204
    assert len(body["results"]) == 200


def test_lists_filter_by_exact_field(api, admin):
    create_user(admin, "alex", "another-long-password", Role.VIEWER)
    create_user(admin, "sam", "another-long-password", Role.EDITOR)

    response = api.get("/api/users/?role=viewer")

    assert [user["username"] for user in response.json()["results"]] == ["alex"]


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("/api/users/?rol=viewer", "rol"),
        ("/api/users/?role=owner", "role"),
        ("/api/tokens/?scope=everyone", "scope"),
    ],
)
def test_unknown_filters_and_values_are_errors(api, path, field):
    response = api.get(path)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert field in response.json()["error"]["fields"]


def test_validation_errors_share_one_shape(api):
    response = api.post(
        "/api/users/",
        {"username": "alex", "password": "short", "role": "viewer"},
        content_type="application/json",
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    assert "password" in error["fields"]


@pytest.mark.parametrize(
    ("request_kwargs", "status", "code"),
    [
        ({"path": "/api/users/999999/", "method": "PATCH", "data": {"role": "viewer"}}, 404, "not_found"),
        ({"path": "/api/status/", "method": "POST", "data": {}}, 405, "method_not_allowed"),
        ({"path": "/api/users/?page=99", "method": "GET"}, 404, "not_found"),
    ],
)
def test_other_errors_share_one_shape(api, request_kwargs, status, code):
    method = request_kwargs["method"].lower()
    kwargs = {}
    if "data" in request_kwargs:
        kwargs = {"data": request_kwargs["data"], "content_type": "application/json"}

    response = getattr(api, method)(request_kwargs["path"], **kwargs)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]


def test_malformed_json_is_a_parse_error(api):
    response = api.post("/api/users/", "{not json", content_type="application/json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "parse_error"


def test_unauthenticated_and_forbidden_share_one_shape(admin):
    anonymous = Client().get("/api/users/")
    viewer = create_user(admin, "viewer", "another-long-password", Role.VIEWER)
    forbidden = Client(
        HTTP_AUTHORIZATION=f"Bearer {create_token(viewer, 'viewer').secret}"
    ).get("/api/users/")

    assert anonymous.json()["error"]["code"] == "not_authenticated"
    assert forbidden.json()["error"]["code"] == "permission_denied"


def test_a_database_outage_with_a_token_is_a_503(client, unreachable_database):
    response = client.get("/api/users/", HTTP_AUTHORIZATION="Bearer nat_any-token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
