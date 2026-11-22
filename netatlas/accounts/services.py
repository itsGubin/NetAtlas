"""Users and their Roles."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _

from netatlas.accounts.models import ApiToken, UserRole
from netatlas.accounts.roles import Role

TOKEN_PREFIX = "nat_"


@dataclass(frozen=True)
class UserSummary:
    id: int
    username: str
    role: Role | None


@dataclass(frozen=True)
class TokenSummary:
    id: int
    username: str
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class NewToken:
    """Holds the secret, which exists only here and is never shown again."""

    summary: TokenSummary
    secret: str


def has_admin() -> bool:
    return UserRole.objects.filter(role=Role.ADMIN).exists()


def ensure_initial_admin(username: str, password: str) -> bool:
    """Create the first Admin. Does nothing once any Admin exists, so restarts are safe."""
    with transaction.atomic():
        if has_admin():
            return False
        _create_with_role(username, password, Role.ADMIN)
    return True


def create_user(actor, username: str, password: str, role: Role):
    require_admin(actor)
    return _create_with_role(username, password, role)


def assign_role(actor, user, role: Role) -> None:
    """Change a user's Role. An install always keeps at least one Admin."""
    with transaction.atomic():
        # Locking the Admin rows stops two concurrent demotions from removing the last one.
        admin_ids = list(
            UserRole.objects.select_for_update()
            .filter(role=Role.ADMIN)
            .order_by("pk")
            .values_list("user_id", flat=True)
        )
        require_admin(actor)
        role = _clean_role(role)
        if role != Role.ADMIN and admin_ids == [user.pk]:
            raise ValidationError({"role": [_("NetAtlas must keep at least one Admin.")]})
        UserRole.objects.update_or_create(user=user, defaults={"role": role})


def list_users(actor, role: Role | str | None = None) -> list[UserSummary]:
    require_admin(actor)
    users = get_user_model().objects.select_related("user_role").order_by("username")
    if role is not None:
        users = users.filter(user_role__role=_clean_role(role))
    return [_summary(user) for user in users]


def get_user(actor, user_id: int):
    require_admin(actor)
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        raise Http404
    return user


def summarize(user) -> UserSummary:
    return UserSummary(id=user.pk, username=user.username, role=role_of(user))


def role_of(user) -> Role | None:
    """The user's Role, or None for anonymous users and users without one."""
    if not user.is_authenticated:
        return None
    role = UserRole.objects.filter(user=user).values_list("role", flat=True).first()
    return Role(role) if role else None


def create_token(actor, name: str, expires_at: datetime | None = None) -> NewToken:
    """Create a token for the actor. Nobody can create a token for someone else."""
    if role_of(actor) is None:
        raise PermissionDenied
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        user=actor,
        name=name,
        secret_hash=_hash_secret(secret),
        prefix=secret[:12],
        expires_at=expires_at,
    )
    errors = {}
    try:
        token.clean_fields()
    except ValidationError as error:
        errors.update(error.message_dict)
    if expires_at is not None and expires_at <= timezone.now():
        errors["expires_at"] = [_("The expiry must be in the future.")]
    if errors:
        raise ValidationError(errors)
    token.save()
    return NewToken(summary=_token_summary(token), secret=secret)


def authenticate_token(secret: str):
    """The owner of a usable token, or None if it is unknown, expired or revoked."""
    token = (
        ApiToken.objects.select_related("user")
        .filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
            secret_hash=_hash_secret(secret),
            revoked_at__isnull=True,
            user__is_active=True,
        )
        .first()
    )
    return token.user if token else None


def list_tokens(actor) -> list[TokenSummary]:
    if role_of(actor) is None:
        raise PermissionDenied
    return _token_summaries(ApiToken.objects.filter(user=actor))


def list_all_tokens(actor) -> list[TokenSummary]:
    require_admin(actor)
    return _token_summaries(ApiToken.objects.all())


def revoke_token(actor, token_id: int) -> TokenSummary:
    """Revoke the actor's own token, or any token for an Admin. Others get a 404."""
    with transaction.atomic():
        token = ApiToken.objects.select_for_update().filter(pk=token_id).first()
        if token is None or (token.user_id != actor.pk and role_of(actor) != Role.ADMIN):
            raise Http404
        if token.revoked_at is None:
            token.revoked_at = timezone.now()
            token.save(update_fields=["revoked_at"])
    return _token_summary(token)


def _token_summaries(tokens) -> list[TokenSummary]:
    ordered = tokens.select_related("user").order_by("-created_at", "-pk")
    return [_token_summary(token) for token in ordered]


def _hash_secret(secret: str) -> str:
    # The secret is 256 random bits, so a fast hash is safe and keeps every request cheap.
    return hashlib.sha256(secret.encode()).hexdigest()


def _token_summary(token) -> TokenSummary:
    return TokenSummary(
        id=token.pk,
        username=token.user.username,
        name=token.name,
        prefix=token.prefix,
        created_at=token.created_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
    )


def _summary(user) -> UserSummary:
    user_role = getattr(user, "user_role", None)
    return UserSummary(
        id=user.pk, username=user.username, role=Role(user_role.role) if user_role else None
    )


def _create_with_role(username: str, password: str, role: Role):
    user = get_user_model()(username=username)
    errors = {}
    try:
        user.clean_fields(exclude={"password"})
        user.validate_unique()
    except ValidationError as error:
        errors.update(error.message_dict)
    try:
        validate_password(password, user)
    except ValidationError as error:
        errors["password"] = error.messages
    try:
        role = _clean_role(role)
    except ValidationError as error:
        errors.update(error.message_dict)
    if errors:
        raise ValidationError(errors)

    user.set_password(password)
    try:
        with transaction.atomic():
            user.save()
            UserRole.objects.create(user=user, role=role)
    except IntegrityError as error:
        # Two requests raced for the same username after both passed validate_unique.
        raise ValidationError({"username": [_("This username is already taken.")]}) from error
    return user


def _clean_role(role) -> Role:
    try:
        return Role(role)
    except ValueError as error:
        raise ValidationError({"role": [_("Unknown Role.")]}) from error


def require_admin(actor) -> None:
    """Fails closed: anonymous users and users without a Role are refused."""
    if role_of(actor) != Role.ADMIN:
        raise PermissionDenied


def require_editor(actor) -> None:
    """Admins and Editors may change documented items. Viewers are read-only."""
    if role_of(actor) not in (Role.ADMIN, Role.EDITOR):
        raise PermissionDenied


def require_role(actor) -> None:
    """Any Role may read. Anonymous users and users without a Role may not."""
    if role_of(actor) is None:
        raise PermissionDenied
