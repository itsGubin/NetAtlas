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
    require_editor,
    require_role,
    revoke_token,
    summarize,
)
from netatlas.api.authentication import BearerTokenAuthentication
from netatlas.api.conventions import allowed_filters, paginated
from netatlas.api.serializers import (
    CreatedTokenSerializer,
    CustomerChangeSerializer,
    CustomerSerializer,
    HistoryEntrySerializer,
    NewSiteSerializer,
    NewTokenSerializer,
    NewUserSerializer,
    RoleChangeSerializer,
    SiteChangeSerializer,
    SiteFilterSerializer,
    SiteSerializer,
    StatusSerializer,
    TokenSerializer,
    UserSerializer,
    errors,
    page_of,
)
from netatlas.history.services import item_history
from netatlas.places.services import (
    create_customer,
    create_site,
    delete_customer,
    delete_site,
    get_customer,
    get_site,
    list_customers,
    list_sites,
    update_customer,
    update_site,
)
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


class CustomerListView(APIView):
    @extend_schema(
        operation_id="customers_list",
        parameters=PAGINATION,
        responses={200: page_of(CustomerSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        customers = list_customers(request.user)
        allowed_filters(request)
        return paginated(request, self, customers, asdict)

    @extend_schema(
        request=CustomerChangeSerializer,
        responses={201: CustomerSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = CustomerChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        customer = create_customer(request.user, **body.validated_data)
        return Response(asdict(customer), status=http_status.HTTP_201_CREATED)


class CustomerDetailView(APIView):
    @extend_schema(responses={200: CustomerSerializer, **errors(401, 403, 404)})
    def get(self, request, customer_id):
        return Response(asdict(get_customer(request.user, customer_id)))

    @extend_schema(
        request=CustomerChangeSerializer,
        responses={200: CustomerSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, customer_id):
        require_editor(request.user)
        body = CustomerChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        customer = update_customer(request.user, customer_id, **body.validated_data)
        return Response(asdict(customer))

    @extend_schema(
        description="Deletes the Customer. Its Sites stay, without a Customer.",
        responses={204: None, **errors(401, 403, 404)},
    )
    def delete(self, request, customer_id):
        delete_customer(request.user, customer_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class SiteListView(APIView):
    @extend_schema(
        operation_id="sites_list",
        parameters=[*PAGINATION, OpenApiParameter("customer", int)],
        responses={200: page_of(SiteSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = SiteFilterSerializer(data=allowed_filters(request, "customer"))
        filters.is_valid(raise_exception=True)
        sites = list_sites(request.user, customer_id=filters.validated_data.get("customer"))
        return paginated(request, self, sites, asdict)

    @extend_schema(
        request=NewSiteSerializer,
        responses={201: SiteSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewSiteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        site = create_site(request.user, **body.validated_data)
        return Response(asdict(site), status=http_status.HTTP_201_CREATED)


class SiteDetailView(APIView):
    @extend_schema(responses={200: SiteSerializer, **errors(401, 403, 404)})
    def get(self, request, site_id):
        return Response(asdict(get_site(request.user, site_id)))

    @extend_schema(
        request=SiteChangeSerializer,
        responses={200: SiteSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, site_id):
        require_editor(request.user)
        body = SiteChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_site(request.user, site_id, **body.validated_data)))

    @extend_schema(
        description="Deletes the Site. The last remaining Site cannot be deleted.",
        responses={204: None, **errors(400, 401, 403, 404)},
    )
    def delete(self, request, site_id):
        delete_site(request.user, site_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)
