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
    DeviceChangeSerializer,
    DeviceFilterSerializer,
    DeviceSerializer,
    DeviceTypeChangeSerializer,
    DeviceTypeSerializer,
    HistoryEntrySerializer,
    InterfaceChangeSerializer,
    InterfaceSerializer,
    NewDeviceSerializer,
    NewDeviceTypeSerializer,
    NewInterfaceSerializer,
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
    VirtualDeviceSerializer,
    errors,
    page_of,
)
from netatlas.devices.services import (
    create_device,
    create_device_type,
    create_interface,
    delete_device,
    delete_device_type,
    delete_interface,
    get_device,
    get_device_type,
    get_interface,
    list_device_types,
    list_devices,
    list_interfaces,
    update_device,
    update_device_type,
    update_interface,
    virtual_devices,
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


class DeviceTypeListView(APIView):
    @extend_schema(
        operation_id="device_types_list",
        parameters=PAGINATION,
        responses={200: page_of(DeviceTypeSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        device_types = list_device_types(request.user)
        allowed_filters(request)
        return paginated(request, self, device_types, asdict)

    @extend_schema(
        description="Interface names may be bulk patterns such as `port[1-48]`.",
        request=NewDeviceTypeSerializer,
        responses={201: DeviceTypeSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewDeviceTypeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        device_type = create_device_type(request.user, **body.validated_data)
        return Response(asdict(device_type), status=http_status.HTTP_201_CREATED)


class DeviceTypeDetailView(APIView):
    @extend_schema(responses={200: DeviceTypeSerializer, **errors(401, 403, 404)})
    def get(self, request, device_type_id):
        return Response(asdict(get_device_type(request.user, device_type_id)))

    @extend_schema(
        description="Devices already created from this Device Type keep their copy.",
        request=DeviceTypeChangeSerializer,
        responses={200: DeviceTypeSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, device_type_id):
        require_editor(request.user)
        body = DeviceTypeChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        device_type = update_device_type(request.user, device_type_id, **body.validated_data)
        return Response(asdict(device_type))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, device_type_id):
        delete_device_type(request.user, device_type_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class DeviceListView(APIView):
    @extend_schema(
        operation_id="devices_list",
        parameters=[
            *PAGINATION,
            OpenApiParameter("site", int),
            OpenApiParameter("kind", str, enum=["physical", "virtual"]),
            OpenApiParameter("parent", int, description="Direct Virtual Devices of a Hypervisor."),
        ],
        responses={200: page_of(DeviceSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = DeviceFilterSerializer(data=allowed_filters(request, "site", "kind", "parent"))
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        devices = list_devices(
            request.user,
            site_id=values.get("site"),
            kind=values.get("kind"),
            parent_id=values.get("parent"),
        )
        return paginated(request, self, devices, asdict)

    @extend_schema(
        description="A Device Type's Interfaces and height are copied once, at creation.",
        request=NewDeviceSerializer,
        responses={201: DeviceSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewDeviceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        device = create_device(request.user, **body.validated_data)
        return Response(asdict(device), status=http_status.HTTP_201_CREATED)


class DeviceDetailView(APIView):
    @extend_schema(responses={200: DeviceSerializer, **errors(401, 403, 404)})
    def get(self, request, device_id):
        return Response(asdict(get_device(request.user, device_id)))

    @extend_schema(
        description="A new Site also moves everything nested on the Device.",
        request=DeviceChangeSerializer,
        responses={200: DeviceSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, device_id):
        require_editor(request.user)
        body = DeviceChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_device(request.user, device_id, **body.validated_data)))

    @extend_schema(
        description="Deletes the Device and its Interfaces. A Hypervisor must be empty first.",
        responses={204: None, **errors(400, 401, 403, 404)},
    )
    def delete(self, request, device_id):
        delete_device(request.user, device_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class VirtualDeviceListView(APIView):
    @extend_schema(
        description="Everything running on this Device, depth first, with its nesting depth.",
        parameters=PAGINATION,
        responses={200: page_of(VirtualDeviceSerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, device_id):
        entries = virtual_devices(request.user, device_id)
        allowed_filters(request)
        return paginated(request, self, entries, asdict)


class InterfaceListView(APIView):
    @extend_schema(
        operation_id="device_interfaces_list",
        parameters=PAGINATION,
        responses={200: page_of(InterfaceSerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, device_id):
        interfaces = list_interfaces(request.user, device_id)
        allowed_filters(request)
        return paginated(request, self, interfaces, asdict)

    @extend_schema(
        description="A Virtual Device can only have Virtual Interfaces.",
        request=NewInterfaceSerializer,
        responses={201: InterfaceSerializer, **errors(400, 401, 403, 404)},
    )
    def post(self, request, device_id):
        require_editor(request.user)
        body = NewInterfaceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        interface = create_interface(request.user, device_id, **body.validated_data)
        return Response(asdict(interface), status=http_status.HTTP_201_CREATED)


class InterfaceDetailView(APIView):
    @extend_schema(responses={200: InterfaceSerializer, **errors(401, 403, 404)})
    def get(self, request, interface_id):
        return Response(asdict(get_interface(request.user, interface_id)))

    @extend_schema(
        description="The kind is locked while the Interface is in a LAG or is a LAG with members.",
        request=InterfaceChangeSerializer,
        responses={200: InterfaceSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, interface_id):
        require_editor(request.user)
        body = InterfaceChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        interface = update_interface(request.user, interface_id, **body.validated_data)
        return Response(asdict(interface))

    @extend_schema(
        description="Deleting a LAG keeps its members, without a LAG.",
        responses={204: None, **errors(401, 403, 404)},
    )
    def delete(self, request, interface_id):
        delete_interface(request.user, interface_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)
