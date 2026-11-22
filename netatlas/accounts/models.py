from django.conf import settings
from django.db import models

from netatlas.accounts.roles import Role


class UserRole(models.Model):
    """A user's Role. Separate from the user model, which existing installs have already migrated."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_role"
    )
    role = models.CharField(max_length=16, choices=Role.choices)


class ApiToken(models.Model):
    """A named API credential. Only a hash of the secret is stored."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens"
    )
    name = models.CharField(max_length=100)
    secret_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
