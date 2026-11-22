"""OIDC login against a real local provider, so signatures, nonce and PKCE are really checked."""

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from joserfc import jwt
from joserfc.jwk import RSAKey

from netatlas.accounts.models import OidcIdentity
from netatlas.accounts.roles import Role
from netatlas.accounts.services import assign_role, ensure_initial_admin, role_of
from netatlas.config.oidc import OidcConfig, read_oidc_config

CLIENT_ID = "netatlas-client"
CLIENT_SECRET = "client-secret-value"
CALLBACK = "/login/oidc/callback/"


class Provider:
    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "key-1", "use": "sig"})
        self.codes = {}
        self.claims = {}
        self.userinfo = {}
        self.token_changes = {}
        self.signing_key = self.key
        self.omit_id_token = False
        self.announced_issuer = None
        self.bad_nonce = False
        self.token_requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.issuer = f"http://127.0.0.1:{self.server.server_port}/o/netatlas"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def approve(self, authorize_url, **claims):
        """What the provider does once the person has signed in: a redirect with a code."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"query": query, "claims": {"sub": "user-1", **claims}}
        return {"code": code, "state": query["state"]}

    def _id_token(self, record):
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": "wrong" if self.bad_nonce else record["query"]["nonce"],
            **record["claims"],
            **self.claims,
        }
        header = {"alg": "RS256", "kid": self.signing_key.kid or "key-1"}
        return jwt.encode(header, claims, self.signing_key)

    def _token_response(self, form, authorization):
        self.token_requests += 1
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if authorization != expected:
            return 401, {"error": "invalid_client"}
        record = self.codes.pop(form.get("code"), None)
        if record is None or form.get("redirect_uri") != record["query"]["redirect_uri"]:
            return 400, {"error": "invalid_grant"}
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if record["query"].get("code_challenge_method") != "S256" or (
            record["query"].get("code_challenge") != challenge
        ):
            return 400, {"error": "invalid_grant", "error_description": "PKCE"}
        body = {"access_token": "access", "token_type": "Bearer", "expires_in": 300}
        if not self.omit_id_token:
            body["id_token"] = self._id_token(record)
        return 200, {**body, **self.token_changes}

    def _handler(self):
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                base = provider.issuer
                if self.path.endswith("/.well-known/openid-configuration"):
                    self._json(
                        200,
                        {
                            "issuer": provider.announced_issuer or base,
                            "authorization_endpoint": f"{base}/authorize",
                            "token_endpoint": f"{base}/token",
                            "userinfo_endpoint": f"{base}/userinfo",
                            "jwks_uri": f"{base}/jwks",
                            "id_token_signing_alg_values_supported": ["RS256"],
                        },
                    )
                elif self.path.endswith("/jwks"):
                    self._json(200, {"keys": [provider.key.as_dict(private=False)]})
                elif self.path.endswith("/userinfo"):
                    self._json(200, {"sub": "user-1", **provider.userinfo})
                else:
                    self._json(404, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode()
                form = {k: v[0] for k, v in parse_qs(raw).items()}
                status, body = provider._token_response(form, self.headers.get("Authorization"))
                self._json(status, body)

        return Handler


@pytest.fixture
def provider(settings, db):
    running = Provider()
    settings.OIDC = OidcConfig(
        issuer=running.issuer,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        provider_name="Authentik",
        default_role="viewer",
        allowed_email_domains=(),
        redirect_uri="",
    )
    yield running
    running.close()


@pytest.fixture
def admin(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


def configure(settings, **changes):
    values = {**settings.OIDC.__dict__, **changes}
    settings.OIDC = OidcConfig(**values)


def start(client, next_url=""):
    response = client.post("/login/oidc/", {"next": next_url})
    assert response.status_code == 302
    return response["Location"]


def sign_in(client, provider, next_url="", **claims):
    params = provider.approve(start(client, next_url), **claims)
    return client.get(CALLBACK, params)


def logged_in_user(client):
    user_id = client.session.get("_auth_user_id")
    return get_user_model().objects.get(pk=user_id) if user_id else None


def refused(client, response):
    assert response.status_code == 302
    assert response["Location"] == "/login/"
    assert logged_in_user(client) is None
    return client.get("/login/").content.decode()


def test_first_login_creates_a_viewer(client, provider):
    response = sign_in(client, provider, preferred_username="anna", email="anna@example.com")
    assert response.status_code == 302
    assert response["Location"] == "/"
    user = logged_in_user(client)
    assert user.username == "anna"
    assert role_of(user) == Role.VIEWER
    assert not user.has_usable_password()
    assert client.get("/").status_code == 200


def test_default_role_is_configurable(client, provider, settings):
    configure(settings, default_role="editor")
    sign_in(client, provider, preferred_username="anna")
    assert role_of(logged_in_user(client)) == Role.EDITOR


def test_returning_user_is_the_same_and_keeps_their_role(client, provider, admin):
    sign_in(client, provider, preferred_username="anna")
    user = logged_in_user(client)
    assign_role(admin, user, Role.EDITOR)
    client.post("/logout/")

    sign_in(client, provider, preferred_username="renamed")
    again = logged_in_user(client)
    assert again.pk == user.pk
    assert again.username == "anna"
    assert role_of(again) == Role.EDITOR
    assert OidcIdentity.objects.count() == 1


@pytest.mark.parametrize("claimed", ["root", "ROOT", "Root"])
def test_provider_account_never_takes_over_a_local_user(client, provider, admin, claimed):
    sign_in(client, provider, preferred_username=claimed, email="root@example.com")
    user = logged_in_user(client)
    assert user.pk != admin.pk
    assert user.username == f"{claimed}-2"
    assert role_of(user) == Role.VIEWER


def test_same_subject_at_another_issuer_is_another_user(client, provider, settings):
    sign_in(client, provider, preferred_username="anna")
    first = logged_in_user(client)
    OidcIdentity.objects.filter(user=first).update(issuer="https://other.example")
    client.post("/logout/")
    sign_in(client, provider, preferred_username="anna")
    assert logged_in_user(client).pk != first.pk


def test_local_login_still_works_with_oidc_on(client, provider, admin):
    response = client.post(
        "/login/", {"username": "root", "password": "correct-horse-battery"}
    )
    assert response.status_code == 302
    assert logged_in_user(client).pk == admin.pk


def test_oidc_user_cannot_use_the_password_form(client, provider):
    sign_in(client, provider, preferred_username="anna")
    client.post("/logout/")
    response = client.post("/login/", {"username": "anna", "password": ""})
    assert response.status_code == 200
    assert logged_in_user(client) is None


def test_login_page_shows_the_provider_only_when_configured(client, db, settings):
    settings.OIDC = None
    assert "Log in with" not in client.get("/login/").content.decode()
    assert client.post("/login/oidc/").status_code == 404
    assert client.get(CALLBACK).status_code == 404


def test_login_page_button(client, provider):
    assert "Log in with Authentik" in client.get("/login/").content.decode()


def test_start_needs_post_and_csrf(provider):
    assert Client().get("/login/oidc/").status_code == 405
    assert Client(enforce_csrf_checks=True).post("/login/oidc/").status_code == 403


def test_redirect_uses_pkce_state_and_nonce(client, provider):
    query = parse_qs(urlsplit(start(client)).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0] and query["nonce"][0] and query["code_challenge"][0]
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == [f"http://testserver{CALLBACK}"]


def test_configured_redirect_uri_is_used(client, provider, settings):
    configure(settings, redirect_uri="https://netatlas.example/login/oidc/callback/")
    query = parse_qs(urlsplit(start(client)).query)
    assert query["redirect_uri"] == ["https://netatlas.example/login/oidc/callback/"]


def test_goes_back_to_the_wanted_page(client, provider):
    response = sign_in(client, provider, next_url="/devices/", preferred_username="anna")
    assert response["Location"] == "/devices/"


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "javascript:x"])
def test_never_redirects_to_another_site(client, provider, target):
    response = sign_in(client, provider, next_url=target, preferred_username="anna")
    assert response["Location"] == "/"


def test_wrong_state_is_refused(client, provider):
    params = provider.approve(start(client))
    response = client.get(CALLBACK, {**params, "state": "forged"})
    assert "could not be completed" in refused(client, response)
    assert provider.token_requests == 0


def test_callback_from_another_browser_is_refused(client, provider):
    params = provider.approve(start(client))
    other = Client()
    response = other.get(CALLBACK, params)
    assert response.status_code == 302
    assert logged_in_user(other) is None


def test_replayed_callback_is_refused(client, provider):
    params = provider.approve(start(client), preferred_username="anna")
    assert logged_in_user(client) is None
    client.get(CALLBACK, params)
    client.post("/logout/")
    refused(client, client.get(CALLBACK, params))


def test_provider_error_is_refused(client, provider):
    query = parse_qs(urlsplit(start(client)).query)
    response = client.get(CALLBACK, {"error": "access_denied", "state": query["state"][0]})
    refused(client, response)


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-client"},
        {"iss": "https://evil.example"},
        {"exp": 1000},
        {"sub": ""},
        {"sub": "x" * 300},
    ],
)
def test_bad_id_token_claims_are_refused(client, provider, claims):
    provider.claims = claims
    refused(client, sign_in(client, provider, preferred_username="anna"))
    assert not OidcIdentity.objects.exists()


def test_wrong_nonce_is_refused(client, provider):
    provider.bad_nonce = True
    refused(client, sign_in(client, provider, preferred_username="anna"))


def test_token_signed_by_another_key_is_refused(client, provider):
    provider.signing_key = RSAKey.generate_key(2048, parameters={"kid": "key-1"})
    refused(client, sign_in(client, provider, preferred_username="anna"))


def test_unsigned_token_is_refused(client, provider):
    def encode(segment):
        data = json.dumps(segment).encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    url = start(client)
    params = provider.approve(url, preferred_username="anna")
    nonce = parse_qs(urlsplit(url).query)["nonce"][0]
    now = int(time.time())
    claims = {
        "iss": provider.issuer,
        "aud": CLIENT_ID,
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
        "nonce": nonce,
    }
    provider.token_changes = {
        "id_token": f"{encode({'alg': 'none'})}.{encode(claims)}."
    }
    refused(client, client.get(CALLBACK, params))


def test_response_without_id_token_is_refused(client, provider):
    provider.omit_id_token = True
    refused(client, sign_in(client, provider, preferred_username="anna"))


def test_provider_announcing_another_issuer_is_refused(client, provider):
    provider.announced_issuer = "https://evil.example"
    response = client.post("/login/oidc/")
    assert response["Location"] == "/login/"
    assert "cannot be reached" in client.get("/login/").content.decode()


def test_provider_down_is_a_message_not_an_error(client, provider, settings):
    configure(settings, issuer="http://127.0.0.1:1/o/down")
    response = client.post("/login/oidc/")
    assert response.status_code == 302
    assert response["Location"] == "/login/"
    assert "cannot be reached" in client.get("/login/").content.decode()


def test_rotated_signing_key_still_works(client, provider):
    url = start(client)
    provider.key = RSAKey.generate_key(2048, parameters={"kid": "key-2", "use": "sig"})
    provider.signing_key = provider.key
    client.get(CALLBACK, provider.approve(url, preferred_username="anna"))
    assert logged_in_user(client).username == "anna"


def test_userinfo_fills_missing_claims(client, provider):
    provider.userinfo = {"preferred_username": "from-userinfo", "email": "a@example.com"}
    sign_in(client, provider)
    assert logged_in_user(client).username == "from-userinfo"


def test_userinfo_about_another_subject_is_refused(client, provider):
    provider.userinfo = {"sub": "someone-else", "preferred_username": "anna"}
    refused(client, sign_in(client, provider))


def test_signed_claims_beat_userinfo(client, provider):
    provider.userinfo = {"preferred_username": "from-userinfo"}
    sign_in(client, provider, preferred_username="from-token")
    assert logged_in_user(client).username == "from-token"


@pytest.mark.parametrize(
    "claims, allowed",
    [
        ({"email": "a@example.com", "email_verified": True}, True),
        ({"email": "a@EXAMPLE.com", "email_verified": "true"}, True),
        ({"email": "a@example.com", "email_verified": False}, False),
        ({"email": "a@example.com"}, False),
        ({"email": "a@evil.example", "email_verified": True}, False),
        ({"email": "a@example.com.evil.example", "email_verified": True}, False),
        ({"email_verified": True}, False),
    ],
)
def test_allowed_email_domains(client, provider, settings, claims, allowed):
    configure(settings, allowed_email_domains=("example.com",))
    provider.userinfo = {"preferred_username": "anna"}
    response = sign_in(client, provider, **claims)
    if allowed:
        assert logged_in_user(client) is not None
    else:
        assert "not allowed" in refused(client, response)
        assert not OidcIdentity.objects.exists()


def test_narrowing_the_domains_locks_out_existing_users(client, provider, settings):
    sign_in(client, provider, preferred_username="anna", email="a@old.example", email_verified=True)
    client.post("/logout/")
    configure(settings, allowed_email_domains=("new.example",))
    response = sign_in(
        client, provider, preferred_username="anna", email="a@old.example", email_verified=True
    )
    refused(client, response)


def test_deactivated_user_cannot_log_in(client, provider):
    sign_in(client, provider, preferred_username="anna")
    user = logged_in_user(client)
    client.post("/logout/")
    user.is_active = False
    user.save()
    assert "deactivated" in refused(client, sign_in(client, provider))


@pytest.mark.parametrize(
    "claims, expected",
    [
        ({"preferred_username": "anna maria/../x"}, "anna-maria-..-x"),
        ({"email": "bob@example.com"}, "bob"),
        ({}, "oidc-user"),
        ({"preferred_username": "<script>"}, "script"),
    ],
)
def test_usernames_are_cleaned(client, provider, claims, expected):
    sign_in(client, provider, **claims)
    assert logged_in_user(client).username == expected


ISSUER = "https://auth.example.com/o/netatlas/"
FULL = {
    "NETATLAS_OIDC_ISSUER": ISSUER,
    "NETATLAS_OIDC_CLIENT_ID": "id",
    "NETATLAS_OIDC_CLIENT_SECRET": "secret",
}


def test_config_is_off_without_settings():
    assert read_oidc_config({}) is None
    assert read_oidc_config({"NETATLAS_OIDC_ISSUER": " "}) is None


def test_config_defaults():
    config = read_oidc_config(FULL)
    assert config.issuer == ISSUER.rstrip("/")
    assert config.default_role == "viewer"
    assert config.provider_name == "OIDC"


@pytest.mark.parametrize(
    "changes",
    [
        {"NETATLAS_OIDC_CLIENT_SECRET": ""},
        {"NETATLAS_OIDC_CLIENT_ID": ""},
        {"NETATLAS_OIDC_ISSUER": ""},
        {"NETATLAS_OIDC_ISSUER": "http://auth.example.com/"},
        {"NETATLAS_OIDC_ISSUER": "ftp://auth.example.com/"},
        {"NETATLAS_OIDC_DEFAULT_ROLE": "admin"},
        {"NETATLAS_OIDC_DEFAULT_ROLE": "nonsense"},
        {"NETATLAS_OIDC_ISSUER": "https://accounts.google.com"},
    ],
)
def test_unsafe_config_stops_the_start(changes):
    with pytest.raises(ImproperlyConfigured):
        read_oidc_config({**FULL, **changes})


def test_google_works_with_a_domain_list():
    config = read_oidc_config(
        {
            **FULL,
            "NETATLAS_OIDC_ISSUER": "https://accounts.google.com",
            "NETATLAS_OIDC_ALLOWED_EMAIL_DOMAINS": "Example.com, example.org",
        }
    )
    assert config.allowed_email_domains == ("example.com", "example.org")
