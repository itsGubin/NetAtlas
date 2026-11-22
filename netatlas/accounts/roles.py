"""The global Roles. Kept apart from the models so adapters can import them (ADR 0003)."""

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


class Role(TextChoices):
    ADMIN = "admin", _("Admin")
    EDITOR = "editor", _("Editor")
    VIEWER = "viewer", _("Viewer")
