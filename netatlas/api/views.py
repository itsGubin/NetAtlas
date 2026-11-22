from dataclasses import asdict

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status as http_status
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as ApiValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from netatlas.accounts.roles import Role
from netatlas.accounts.services import (
    assign_role,
    create_token,
    create_user,
    get_user,
    list_all_tokens,
    list_tokens,
    list_users,
    require_admin,
    revoke_token,
    summarize,
)
from netatlas.api.authentication import BearerTokenAuthentication
from netatlas.api.conventions import allowed_filters, paginated
from netatlas.api.serializers import (
    CreatedTokenSerializer,
    HistoryEntrySerializer,
    NewTokenSerializer,
    NewUserSerializer,
    RoleChangeSerializer,
    StatusSerializer,
    TokenSerializer,
    UserSerializer,
    errors,
    page_of,
)
from netatlas.history.services import item_history
from netatlas.system.services import get_system_status

PAGINATION = [
    OpenApiParameter("page", int, description="Page number, starting at 1."),
    OpenApiParameter("page_size", int, description="Items per page. Default 50, maximum 200."),
]


class StatusView(APIView):
    """Instance health, used by the Compose healthcheck. Public, and touches no session."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: StatusSerializer, 503: StatusSerializer}, auth=[])
    def get(self, request):
        status = get_system_status()
        return Response(asdict(status), status=200 if status.is_healthy else 503)


class UserListView(APIView):
    @extend_schema(
        parameters=[*PAGINATION, OpenApiParameter("role", str, enum=Role.values)],
        responses={200: page_of(UserSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        # Check the Role first, so a non-Admin learns nothing from validation errors.
        require_admin(request.user)
        filters = allowed_filters(request, "role")
        users = list_users(request.user, role=filters.get("role"))
        return paginated(request, self, users, asdict)

    @extend_schema(
        request=NewUserSerializer,
        responses={201: UserSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_admin(request.user)
        body = NewUserSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user = create_user(request.user, **body.validated_data)
        return Response(asdict(summarize(user)), status=http_status.HTTP_201_CREATED)


class UserDetailView(APIView):
    @extend_schema(
        request=RoleChangeSerializer,
        responses={200: UserSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, user_id):
        user = get_user(request.user, user_id)
        body = RoleChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        assign_role(request.user, user, body.validated_data["role"])
        return Response(asdict(summarize(user)))


class TokenListView(APIView):
    @extend_schema(
        parameters=[
            *PAGINATION,
            OpenApiParameter(
                "scope", str, enum=["own", "all"], description="`all` requires the Admin Role."
            ),
        ],
        responses={200: page_of(TokenSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        scope = allowed_filters(request, "scope").get("scope", "own")
        if scope not in ("own", "all"):
            raise ApiValidationError({"scope": [_("Must be own or all.")]})
        tokens = list_all_tokens(request.user) if scope == "all" else list_tokens(request.user)
        return paginated(request, self, tokens, asdict)

    @extend_schema(
        description="Requires a logged-in session. A token cannot create another token.",
        request=NewTokenSerializer,
        responses={201: CreatedTokenSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        # A leaked token must not be able to outlive its own revocation by minting another.
        if isinstance(request.successful_authenticator, BearerTokenAuthentication):
            raise PermissionDenied(_("Tokens can only be created from a logged-in session."))
        body = NewTokenSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        token = create_token(request.user, **body.validated_data)
        return Response(
            {**asdict(token.summary), "secret": token.secret},
            status=http_status.HTTP_201_CREATED,
        )


class TokenDetailView(APIView):
    @extend_schema(responses={204: None, **errors(401, 404)})
    def delete(self, request, token_id):
        revoke_token(request.user, token_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class HistoryView(APIView):
    @extend_schema(
        description="An item's History, newest first. It stays available after a delete.",
        parameters=PAGINATION,
        responses={200: page_of(HistoryEntrySerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, kind, item_id):
        entries = item_history(request.user, kind, item_id)
        allowed_filters(request)
        return paginated(request, self, entries, asdict)
