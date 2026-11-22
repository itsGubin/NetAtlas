"""Login through an OIDC provider, alongside local accounts.

Authlib does the protocol: discovery, state, nonce, PKCE and checking the ID Token. A provider
account is tied to a NetAtlas user only by issuer and subject. Email and username are never used
to find a user, so a provider account can never take over an existing one.
"""

import logging
import re

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from netatlas.accounts.models import OidcIdentity, UserRole
from netatlas.accounts.roles import Role

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 5
MAX_SUBJECT_LENGTH = 255
_USERNAME_LENGTH = 140
_NOT_USERNAME = re.compile(r"[^\w.@+-]+")


class OidcLoginFailed(Exception):
    """The login did not succeed. The message is safe to show."""


def provider_name() -> str | None:
    """The name for the login button, or None if OIDC is off."""
    return settings.OIDC.provider_name if settings.OIDC else None


def begin_login(request, redirect_uri: str):
    """The redirect to the provider."""
    try:
        return _client().authorize_redirect(request, settings.OIDC.redirect_uri or redirect_uri)
    except Exception as error:
        logger.warning("OIDC login could not start: %s", error)
        raise OidcLoginFailed(_("The login provider cannot be reached.")) from error


def finish_login(request):
    """The user for this provider response. Raises OidcLoginFailed for anything else."""
    try:
        claims = _verified_claims(request)
    except Exception as error:
        logger.warning("OIDC login refused: %s", error)
        raise OidcLoginFailed(_("The login could not be completed.")) from error
    return user_for_claims(settings.OIDC.issuer, claims)


def user_for_claims(issuer: str, claims: dict):
    """The user tied to this issuer and subject, created on first login."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > MAX_SUBJECT_LENGTH:
        raise OidcLoginFailed(_("The login could not be completed."))
    # Checked on every login, so narrowing the list also locks out existing users.
    _require_allowed_email(claims)

    user = _existing_user(issuer, subject)
    if user is None:
        try:
            with transaction.atomic():
                user = _create_user(issuer, subject, claims)
        except IntegrityError:
            # Two first logins at once: the other one created the user.
            user = _existing_user(issuer, subject)
            if user is None:
                raise
    if not user.is_active:
        raise OidcLoginFailed(_("This account is deactivated."))
    return user


def _client():
    config = settings.OIDC
    oauth = OAuth()
    # A new client per login, so a provider's changed keys or endpoints are always picked up.
    client = oauth.register(
        name="netatlas",
        client_id=config.client_id,
        client_secret=config.client_secret,
        server_metadata_url=f"{config.issuer}/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",
            "default_timeout": HTTP_TIMEOUT_SECONDS,
        },
    )
    announced = client.load_server_metadata().get("issuer", "")
    if announced.rstrip("/") != config.issuer:
        raise ValueError(f"Provider announces another issuer: {announced}")
    return client


def _verified_claims(request) -> dict:
    client = _client()
    token = client.authorize_access_token(request)
    # Authlib skips the ID Token check when the response has none, so that is refused here.
    if not token.get("userinfo"):
        raise ValueError("The provider sent no ID Token.")
    claims = dict(token["userinfo"])
    if _needs_userinfo(claims) and client.load_server_metadata().get("userinfo_endpoint"):
        extra = dict(client.userinfo(token=token))
        if extra.get("sub") != claims["sub"]:
            raise ValueError("Userinfo is about another subject.")
        # What the signed ID Token says always wins.
        claims = {**extra, **claims}
    return claims


def _needs_userinfo(claims: dict) -> bool:
    return not (claims.get("preferred_username") and claims.get("email"))


def _require_allowed_email(claims: dict) -> None:
    domains = settings.OIDC.allowed_email_domains
    if not domains:
        return
    email = claims.get("email")
    verified = claims.get("email_verified") in (True, "true")
    domain = email.rpartition("@")[2].lower() if isinstance(email, str) else ""
    if not verified or domain not in domains:
        raise OidcLoginFailed(_("This account is not allowed to log in."))


def _existing_user(issuer: str, subject: str):
    identity = (
        OidcIdentity.objects.select_related("user").filter(issuer=issuer, subject=subject).first()
    )
    return identity.user if identity else None


def _create_user(issuer: str, subject: str, claims: dict):
    users = get_user_model()
    # No password: this user can only log in through the provider.
    user = users.objects.create_user(_free_username(claims), password=None)
    UserRole.objects.create(user=user, role=Role(settings.OIDC.default_role))
    OidcIdentity.objects.create(user=user, issuer=issuer, subject=subject)
    return user


def _free_username(claims: dict) -> str:
    """A name nobody has in any spelling, so a provider can't imitate `root` as `Root`."""
    email = claims.get("email")
    wanted = claims.get("preferred_username") or (
        email.partition("@")[0] if isinstance(email, str) else ""
    )
    base = _NOT_USERNAME.sub("-", wanted if isinstance(wanted, str) else "").strip("-.")
    base = base[:_USERNAME_LENGTH] or "oidc-user"
    users = get_user_model().objects
    name = base
    suffix = 1
    while users.filter(username__iexact=name).exists():
        suffix += 1
        name = f"{base}-{suffix}"
    return name
