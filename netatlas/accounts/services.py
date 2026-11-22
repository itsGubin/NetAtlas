"""Users and their Roles."""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils.translation import gettext as _

from netatlas.accounts.models import UserRole
from netatlas.accounts.roles import Role


@dataclass(frozen=True)
class UserSummary:
    id: int
    username: str
    role: Role | None


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


def list_users(actor) -> list[UserSummary]:
    require_admin(actor)
    users = get_user_model().objects.select_related("user_role").order_by("username")
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
