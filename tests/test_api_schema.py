import json

import pytest
from django.contrib.auth import authenticate
from django.core.management import call_command
from django.test import Client

from netatlas.accounts.services import create_token, ensure_initial_admin


@pytest.fixture
def api(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    admin = authenticate(username="root", password="correct-horse-battery")
    return Client(HTTP_AUTHORIZATION=f"Bearer {create_token(admin, 'tests').secret}")


@pytest.fixture
def schema(api):
    response = api.get("/api/schema/")
    assert response.status_code == 200
    return json.loads(response.content)


def test_schema_requires_authentication(db):
    assert Client().get("/api/schema/").status_code == 401


def test_schema_is_valid_and_has_no_generator_warnings(tmp_path):
    call_command("spectacular", "--validate", "--fail-on-warn", "--file", tmp_path / "schema.yml")


def test_schema_lists_every_endpoint(schema):
    assert {
        "/api/status/",
        "/api/users/",
        "/api/users/{user_id}/",
        "/api/tokens/",
        "/api/tokens/{token_id}/",
    } <= set(schema["paths"])


def test_schema_documents_bearer_tokens(schema):
    scheme = schema["components"]["securitySchemes"]["bearerAuth"]

    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert {"bearerAuth": []} in schema["paths"]["/api/users/"]["get"]["security"]


def test_status_is_documented_as_public(schema):
    assert schema["paths"]["/api/status/"]["get"].get("security") in ([], [{}], None)


def test_schema_documents_pagination_and_the_error_shape(schema):
    operation = schema["paths"]["/api/users/"]["get"]

    assert {"page", "page_size", "role"} <= {p["name"] for p in operation["parameters"]}
    page = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in page
    page_schema = schema["components"]["schemas"][page["$ref"].rsplit("/", 1)[1]]
    assert {"count", "next", "previous", "results"} <= set(page_schema["properties"])
    assert "403" in operation["responses"]
    error = operation["responses"]["403"]["content"]["application/json"]["schema"]["$ref"]
    assert error.endswith("/Error")


def test_schema_never_documents_a_password_in_a_response(schema):
    operation = schema["paths"]["/api/users/"]["post"]
    response = operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    properties = schema["components"]["schemas"][response.rsplit("/", 1)[1]]["properties"]

    assert "password" not in properties
