from django.utils.translation import gettext as _
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from netatlas.accounts.services import authenticate_token


class BearerTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <token>`. Tokens act with their owner's current Role."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed(_("Malformed Authorization header."))
        try:
            secret = parts[1].decode()
        except UnicodeError as error:
            raise AuthenticationFailed(_("Invalid token.")) from error

        user = authenticate_token(secret)
        if user is None:
            raise AuthenticationFailed(_("Invalid token."))
        return (user, None)

    def authenticate_header(self, request):
        return "Bearer"


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = "netatlas.api.authentication.BearerTokenAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer"}
