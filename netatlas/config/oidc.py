"""Reads the OIDC settings from the environment. A wrong setup stops NetAtlas from starting."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Anyone in the world has an account at these, so they need a list of allowed domains.
_PUBLIC_ISSUER_HOSTS = {"accounts.google.com"}
_DEFAULT_ROLES = {"viewer", "editor"}


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    provider_name: str
    default_role: str
    allowed_email_domains: tuple[str, ...]
    redirect_uri: str


def read_oidc_config(environ) -> OidcConfig | None:
    """The OIDC setup, or None if OIDC is not configured."""

    def value(name, default=""):
        return (environ.get(f"NETATLAS_OIDC_{name}") or default).strip()

    issuer = value("ISSUER")
    client_id = value("CLIENT_ID")
    client_secret = value("CLIENT_SECRET")
    if not (issuer or client_id or client_secret):
        return None
    if not (issuer and client_id and client_secret):
        raise ImproperlyConfigured(
            "OIDC needs NETATLAS_OIDC_ISSUER, NETATLAS_OIDC_CLIENT_ID "
            "and NETATLAS_OIDC_CLIENT_SECRET together."
        )

    parts = urlsplit(issuer)
    if parts.scheme != "https" and not (
        parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    ):
        raise ImproperlyConfigured("NETATLAS_OIDC_ISSUER must be an https:// URL.")

    role = value("DEFAULT_ROLE", "viewer").lower()
    if role not in _DEFAULT_ROLES:
        raise ImproperlyConfigured("NETATLAS_OIDC_DEFAULT_ROLE must be viewer or editor.")

    domains = tuple(
        sorted({d.strip().lower() for d in value("ALLOWED_EMAIL_DOMAINS").split(",") if d.strip()})
    )
    if parts.hostname in _PUBLIC_ISSUER_HOSTS and not domains:
        raise ImproperlyConfigured(
            "This provider has public accounts. Set NETATLAS_OIDC_ALLOWED_EMAIL_DOMAINS."
        )

    return OidcConfig(
        issuer=issuer.rstrip("/"),
        client_id=client_id,
        client_secret=client_secret,
        provider_name=value("PROVIDER_NAME", "OIDC"),
        default_role=role,
        allowed_email_domains=domains,
        redirect_uri=value("REDIRECT_URI"),
    )
