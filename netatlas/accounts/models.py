from django.conf import settings
from django.db import models

from netatlas.accounts.roles import Role


class UserRole(models.Model):
    """A user's Role. Separate from the user model, which existing installs have already migrated."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_role"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
