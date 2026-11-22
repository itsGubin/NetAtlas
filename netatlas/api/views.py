from dataclasses import asdict

from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status as http_status
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as ApiValidationError
from rest_framework.permissions import AllowAny
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.parsers import MultiPartParser
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
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    create_vlan,
    delete_dhcp_range,
    delete_ip_address,
    delete_subnet,
    delete_vlan,
    get_dhcp_range,
    get_ip_address,
    get_subnet,
    get_vlan,
    list_dhcp_ranges,
    list_ip_addresses,
    list_subnets,
    list_vlans,
    subnet_usage,
    update_dhcp_range,
    update_ip_address,
    update_subnet,
    update_vlan,
    vlan_usage,
)
from netatlas.api.authentication import BearerTokenAuthentication
from netatlas.api.conventions import allowed_filters, paginated, with_tags
from netatlas.api.serializers import (
    LiveFieldOptionSerializer,
    LiveFieldValueSerializer,
    MentionOptionSerializer,
    AttachmentSerializer,
    AttachmentUploadSerializer,
    PageChangeSerializer,
    PageTemplateChangeSerializer,
    PageTemplateCreateSerializer,
    PageTemplatePreviewSerializer,
    PageTemplateSerializer,
    RemovedSectionSerializer,
    PageCreateSerializer,
    PageSerializer,
    PageSummarySerializer,
    AssignIpAddressSerializer,
    CableChangeSerializer,
    CableFilterSerializer,
    CableSerializer,
    CableTraceSerializer,
    CreatedTokenSerializer,
    CustomerChangeSerializer,
    CustomerSerializer,
    DeletePreviewSerializer,
    DeviceChangeSerializer,
    DeviceFilterSerializer,
    DevicePlacementSerializer,
    DeviceSerializer,
    DeviceTypeChangeSerializer,
    DeviceTypeSerializer,
    DhcpRangeChangeSerializer,
    DhcpRangeFilterSerializer,
    DhcpRangeSerializer,
    HistoryEntrySerializer,
    InterfaceChangeSerializer,
    InterfacePairSerializer,
    InterfaceSerializer,
    InterfaceVlansSerializer,
    IpAddressChangeSerializer,
    IpAddressFilterSerializer,
    IpAddressSerializer,
    NewCableSerializer,
    NewDeviceSerializer,
    NewDeviceTypeSerializer,
    NewDhcpRangeSerializer,
    NewInterfaceSerializer,
    NewIpAddressSerializer,
    NewRackSerializer,
    NewSiteSerializer,
    NewSubnetSerializer,
    NewVlanSerializer,
    NewTokenSerializer,
    NewUserSerializer,
    RackChangeSerializer,
    RackElevationSerializer,
    RackFilterSerializer,
    RackSerializer,
    RoleChangeSerializer,
    SiteChangeSerializer,
    SiteFilterSerializer,
    SiteSerializer,
    StatusSerializer,
    SubnetChangeSerializer,
    SubnetFilterSerializer,
    SubnetSerializer,
    SubnetUsageSerializer,
    TagChangeSerializer,
    TagRefSerializer,
    TagSerializer,
    TagUsageSerializer,
    TokenSerializer,
    UserSerializer,
    VirtualDeviceSerializer,
    VlanChangeSerializer,
    VlanFilterSerializer,
    VlanSerializer,
    VlanUsageSerializer,
    errors,
    page_of,
)
from netatlas.deletion.services import preview_delete
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
    delete_cable,
    delete_device,
    delete_device_type,
    delete_interface,
    delete_rack,
    get_cable,
    get_device,
    get_device_type,
    get_interface,
    get_rack,
    list_cables,
    list_device_types,
    list_devices,
    list_interfaces,
    list_racks,
    pair_interfaces,
    place_device,
    rack_elevation,
    set_interface_vlans,
    trace_interface,
    unpair_interface,
    unrack_device,
    update_cable,
    update_device,
    update_device_type,
    update_interface,
    update_rack,
    virtual_devices,
)
from netatlas.history.services import item_history
from netatlas.pages.attachments import (
    add_attachment,
    attachment_response,
    delete_attachment,
    get_attachment,
    list_attachments,
)
from netatlas.pages.services import (
    UnconfirmedSectionRemoval,
    create_page_template,
    delete_page_template,
    get_page_template,
    list_page_templates,
    preview_template_change,
    update_page_template,
    create_page,
    delete_page,
    get_item_page,
    get_page,
    list_pages,
    live_field_options,
    live_field_values,
    mention_options,
    referenced_by,
    update_page,
)
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
from netatlas.tagging.services import (
    add_tag,
    create_tag,
    delete_tag,
    list_tags,
    remove_tag,
    tag_usage,
    tags_of,
    update_tag,
)

PAGINATION = [
    OpenApiParameter("page", int, description="Page number, starting at 1."),
    OpenApiParameter("page_size", int, description="Items per page. Default 50, maximum 200."),
]
TAG_FILTER = OpenApiParameter(
    "tag", str, many=True, description="Repeat to require several Tags. Any spelling matches."
)


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


class DeletePreviewView(APIView):
    @extend_schema(
        description="What deleting this item would do. Nothing is changed.",
        responses={200: DeletePreviewSerializer, **errors(400, 401, 403, 404)},
    )
    def get(self, request, kind, item_id):
        preview = preview_delete(request.user, kind, item_id)
        allowed_filters(request)
        return Response(asdict(preview))


class CustomerListView(APIView):
    @extend_schema(
        operation_id="customers_list",
        parameters=[*PAGINATION, TAG_FILTER],
        responses={200: page_of(CustomerSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        customers = list_customers(request.user)
        allowed_filters(request, "tag")
        return paginated(request, self, with_tags(request, "customer", customers), asdict)

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
        parameters=[*PAGINATION, TAG_FILTER, OpenApiParameter("customer", int)],
        responses={200: page_of(SiteSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = SiteFilterSerializer(data=allowed_filters(request, "customer", "tag"))
        filters.is_valid(raise_exception=True)
        sites = list_sites(request.user, customer_id=filters.validated_data.get("customer"))
        return paginated(request, self, with_tags(request, "site", sites), asdict)

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
        parameters=[*PAGINATION, TAG_FILTER],
        responses={200: page_of(DeviceTypeSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        device_types = list_device_types(request.user)
        allowed_filters(request, "tag")
        return paginated(request, self, with_tags(request, "device-type", device_types), asdict)

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
            TAG_FILTER,
            OpenApiParameter("site", int),
            OpenApiParameter("kind", str, enum=["physical", "virtual"]),
            OpenApiParameter("parent", int, description="Direct Virtual Devices of a Hypervisor."),
        ],
        responses={200: page_of(DeviceSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = DeviceFilterSerializer(
            data=allowed_filters(request, "site", "kind", "parent", "tag")
        )
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        devices = list_devices(
            request.user,
            site_id=values.get("site"),
            kind=values.get("kind"),
            parent_id=values.get("parent"),
        )
        return paginated(request, self, with_tags(request, "device", devices), asdict)

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
        parameters=[*PAGINATION, TAG_FILTER],
        responses={200: page_of(InterfaceSerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, device_id):
        interfaces = list_interfaces(request.user, device_id)
        allowed_filters(request, "tag")
        return paginated(request, self, with_tags(request, "interface", interfaces), asdict)

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


class InterfaceTraceView(APIView):
    @extend_schema(
        description="The whole path through a Physical Interface, end to end.",
        responses={200: CableTraceSerializer, **errors(400, 401, 403, 404)},
    )
    def get(self, request, interface_id):
        return Response(asdict(trace_interface(request.user, interface_id)))


class InterfacePairView(APIView):
    @extend_schema(
        description="Pair two Physical Interfaces of one Device, such as a patch panel's sides.",
        request=InterfacePairSerializer,
        responses={200: InterfaceSerializer, **errors(400, 401, 403, 404)},
    )
    def put(self, request, interface_id):
        require_editor(request.user)
        body = InterfacePairSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        interface = pair_interfaces(
            request.user, interface_id, body.validated_data["interface_id"]
        )
        return Response(asdict(interface))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, interface_id):
        unpair_interface(request.user, interface_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class DevicePlacementView(APIView):
    @extend_schema(
        description="Place or move a Physical Device. Overlaps on the same Face are refused.",
        request=DevicePlacementSerializer,
        responses={200: DeviceSerializer, **errors(400, 401, 403, 404)},
    )
    def put(self, request, device_id):
        require_editor(request.user)
        body = DevicePlacementSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(place_device(request.user, device_id, **body.validated_data)))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, device_id):
        unrack_device(request.user, device_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class RackListView(APIView):
    @extend_schema(
        operation_id="racks_list",
        parameters=[*PAGINATION, TAG_FILTER, OpenApiParameter("site", int)],
        responses={200: page_of(RackSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = RackFilterSerializer(data=allowed_filters(request, "site", "tag"))
        filters.is_valid(raise_exception=True)
        racks = list_racks(request.user, site_id=filters.validated_data.get("site"))
        return paginated(request, self, with_tags(request, "rack", racks), asdict)

    @extend_schema(
        request=NewRackSerializer,
        responses={201: RackSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewRackSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        rack = create_rack(request.user, **body.validated_data)
        return Response(asdict(rack), status=http_status.HTTP_201_CREATED)


class RackDetailView(APIView):
    @extend_schema(responses={200: RackSerializer, **errors(401, 403, 404)})
    def get(self, request, rack_id):
        return Response(asdict(get_rack(request.user, rack_id)))

    @extend_schema(
        description="A Rack cannot shrink below its highest Device.",
        request=RackChangeSerializer,
        responses={200: RackSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, rack_id):
        require_editor(request.user)
        body = RackChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_rack(request.user, rack_id, **body.validated_data)))

    @extend_schema(
        description="A Rack that still holds Devices cannot be deleted.",
        responses={204: None, **errors(400, 401, 403, 404)},
    )
    def delete(self, request, rack_id):
        delete_rack(request.user, rack_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class RackElevationView(APIView):
    @extend_schema(
        description="Both Faces from the top unit down, plus zero-height Devices.",
        responses={200: RackElevationSerializer, **errors(401, 403, 404)},
    )
    def get(self, request, rack_id):
        return Response(asdict(rack_elevation(request.user, rack_id)))


class InterfaceVlansView(APIView):
    @extend_schema(
        description="Replaces the Interface's VLANs. All must be in its Device's Site.",
        request=InterfaceVlansSerializer,
        responses={200: InterfaceSerializer, **errors(400, 401, 403, 404)},
    )
    def put(self, request, interface_id):
        require_editor(request.user)
        body = InterfaceVlansSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        interface = set_interface_vlans(request.user, interface_id, **body.validated_data)
        return Response(asdict(interface))


class VlanListView(APIView):
    @extend_schema(
        operation_id="vlans_list",
        parameters=[*PAGINATION, TAG_FILTER, OpenApiParameter("site", int)],
        responses={200: page_of(VlanSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = VlanFilterSerializer(data=allowed_filters(request, "site", "tag"))
        filters.is_valid(raise_exception=True)
        vlans = list_vlans(request.user, site_id=filters.validated_data.get("site"))
        return paginated(request, self, with_tags(request, "vlan", vlans), asdict)

    @extend_schema(
        description="The number is 1 to 4094 and unique within a Site.",
        request=NewVlanSerializer,
        responses={201: VlanSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewVlanSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        vlan = create_vlan(request.user, **body.validated_data)
        return Response(asdict(vlan), status=http_status.HTTP_201_CREATED)


class VlanDetailView(APIView):
    @extend_schema(responses={200: VlanSerializer, **errors(401, 403, 404)})
    def get(self, request, vlan_id):
        return Response(asdict(get_vlan(request.user, vlan_id)))

    @extend_schema(
        request=VlanChangeSerializer,
        responses={200: VlanSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, vlan_id):
        require_editor(request.user)
        body = VlanChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_vlan(request.user, vlan_id, **body.validated_data)))

    @extend_schema(
        description="A VLAN still used by a Subnet or an Interface cannot be deleted.",
        responses={204: None, **errors(400, 401, 403, 404)},
    )
    def delete(self, request, vlan_id):
        delete_vlan(request.user, vlan_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class VlanUsageView(APIView):
    @extend_schema(
        description="The Subnets bound to this VLAN and the Interfaces carrying it.",
        responses={200: VlanUsageSerializer, **errors(401, 403, 404)},
    )
    def get(self, request, vlan_id):
        return Response(asdict(vlan_usage(request.user, vlan_id)))


class IpAddressListView(APIView):
    @extend_schema(
        operation_id="ip_addresses_list",
        description="In numeric order. `subnet` matches the most specific containing Subnet.",
        parameters=[
            *PAGINATION,
            TAG_FILTER,
            OpenApiParameter("site", int),
            OpenApiParameter("subnet", int),
            OpenApiParameter("interface", int),
            OpenApiParameter("status", str, enum=["assigned", "reserved"]),
        ],
        responses={200: page_of(IpAddressSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = IpAddressFilterSerializer(
            data=allowed_filters(request, "site", "subnet", "interface", "status", "tag")
        )
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        addresses = list_ip_addresses(
            request.user,
            site_id=values.get("site"),
            subnet_id=values.get("subnet"),
            interface_id=values.get("interface"),
            status=values.get("status"),
        )
        return paginated(request, self, with_tags(request, "ip-address", addresses), asdict)

    @extend_schema(
        description="Must be inside a Subnet of the Site and outside every DHCP Range.",
        request=NewIpAddressSerializer,
        responses={201: IpAddressSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewIpAddressSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        address = create_ip_address(request.user, **body.validated_data)
        return Response(asdict(address), status=http_status.HTTP_201_CREATED)


class IpAddressDetailView(APIView):
    @extend_schema(responses={200: IpAddressSerializer, **errors(401, 403, 404)})
    def get(self, request, address_id):
        return Response(asdict(get_ip_address(request.user, address_id)))

    @extend_schema(
        description="Setting `interface_id` to null makes the address Reserved.",
        request=IpAddressChangeSerializer,
        responses={200: IpAddressSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, address_id):
        require_editor(request.user)
        body = IpAddressChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        address = update_ip_address(request.user, address_id, **body.validated_data)
        return Response(asdict(address))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, address_id):
        delete_ip_address(request.user, address_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class InterfaceIpAddressesView(APIView):
    @extend_schema(
        description="Assigns the address, using its Reserved entry or documenting it now.",
        request=AssignIpAddressSerializer,
        responses={201: IpAddressSerializer, **errors(400, 401, 403, 404)},
    )
    def post(self, request, interface_id):
        require_editor(request.user)
        body = AssignIpAddressSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        address = assign_ip_address(request.user, interface_id, body.validated_data["address"])
        return Response(asdict(address), status=http_status.HTTP_201_CREATED)


class DhcpRangeListView(APIView):
    @extend_schema(
        operation_id="dhcp_ranges_list",
        parameters=[*PAGINATION, TAG_FILTER, OpenApiParameter("subnet", int)],
        responses={200: page_of(DhcpRangeSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = DhcpRangeFilterSerializer(data=allowed_filters(request, "subnet", "tag"))
        filters.is_valid(raise_exception=True)
        ranges = list_dhcp_ranges(request.user, subnet_id=filters.validated_data.get("subnet"))
        return paginated(request, self, with_tags(request, "dhcp-range", ranges), asdict)

    @extend_schema(
        description="Inclusive at both ends. Cannot overlap IP Addresses or nested Subnets.",
        request=NewDhcpRangeSerializer,
        responses={201: DhcpRangeSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewDhcpRangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        dhcp = create_dhcp_range(request.user, **body.validated_data)
        return Response(asdict(dhcp), status=http_status.HTTP_201_CREATED)


class DhcpRangeDetailView(APIView):
    @extend_schema(responses={200: DhcpRangeSerializer, **errors(401, 403, 404)})
    def get(self, request, range_id):
        return Response(asdict(get_dhcp_range(request.user, range_id)))

    @extend_schema(
        request=DhcpRangeChangeSerializer,
        responses={200: DhcpRangeSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, range_id):
        require_editor(request.user)
        body = DhcpRangeChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_dhcp_range(request.user, range_id, **body.validated_data)))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, range_id):
        delete_dhcp_range(request.user, range_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class SubnetUsageView(APIView):
    @extend_schema(
        description="Used, reserved, DHCP and free space, and the next free IP Address.",
        responses={200: SubnetUsageSerializer, **errors(401, 403, 404)},
    )
    def get(self, request, subnet_id):
        return Response(asdict(subnet_usage(request.user, subnet_id)))


class SubnetListView(APIView):
    @extend_schema(
        operation_id="subnets_list",
        description="In tree order: each Subnet is directly followed by those nested in it.",
        parameters=[
            *PAGINATION,
            TAG_FILTER,
            OpenApiParameter("site", int),
            OpenApiParameter("version", int, enum=[4, 6]),
        ],
        responses={200: page_of(SubnetSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = SubnetFilterSerializer(data=allowed_filters(request, "site", "version", "tag"))
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        subnets = list_subnets(
            request.user, site_id=values.get("site"), version=values.get("version")
        )
        return paginated(request, self, with_tags(request, "subnet", subnets), asdict)

    @extend_schema(
        description="A CIDR with host bits set is refused. Unique within a Site.",
        request=NewSubnetSerializer,
        responses={201: SubnetSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewSubnetSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        subnet = create_subnet(request.user, **body.validated_data)
        return Response(asdict(subnet), status=http_status.HTTP_201_CREATED)


class SubnetDetailView(APIView):
    @extend_schema(responses={200: SubnetSerializer, **errors(401, 403, 404)})
    def get(self, request, subnet_id):
        return Response(asdict(get_subnet(request.user, subnet_id)))

    @extend_schema(
        request=SubnetChangeSerializer,
        responses={200: SubnetSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, subnet_id):
        require_editor(request.user)
        body = SubnetChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_subnet(request.user, subnet_id, **body.validated_data)))

    @extend_schema(
        description="Deletes this Subnet only. Those nested in it stay and move up the tree.",
        responses={204: None, **errors(401, 403, 404)},
    )
    def delete(self, request, subnet_id):
        delete_subnet(request.user, subnet_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class CableListView(APIView):
    @extend_schema(
        operation_id="cables_list",
        parameters=[
            *PAGINATION,
            TAG_FILTER,
            OpenApiParameter("device", int, description="Cables with an end on this Device."),
        ],
        responses={200: page_of(CableSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        require_role(request.user)
        filters = CableFilterSerializer(data=allowed_filters(request, "device", "tag"))
        filters.is_valid(raise_exception=True)
        cables = list_cables(request.user, device_id=filters.validated_data.get("device"))
        return paginated(request, self, with_tags(request, "cable", cables), asdict)

    @extend_schema(
        description="Joins two Physical Interfaces. Each can have one Cable. Sites may differ.",
        request=NewCableSerializer,
        responses={201: CableSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = NewCableSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        cable = create_cable(request.user, **body.validated_data)
        return Response(asdict(cable), status=http_status.HTTP_201_CREATED)


class CableDetailView(APIView):
    @extend_schema(responses={200: CableSerializer, **errors(401, 403, 404)})
    def get(self, request, cable_id):
        return Response(asdict(get_cable(request.user, cable_id)))

    @extend_schema(
        request=CableChangeSerializer,
        responses={200: CableSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, cable_id):
        require_editor(request.user)
        body = CableChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_cable(request.user, cable_id, **body.validated_data)))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, cable_id):
        delete_cable(request.user, cable_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


PAGE_SUMMARY_LIST = page_of(PageSummarySerializer)


class PageListView(APIView):
    @extend_schema(
        operation_id="pages_list",
        parameters=[*PAGINATION, TAG_FILTER],
        responses={200: PAGE_SUMMARY_LIST, **errors(400, 401, 403)},
    )
    def get(self, request):
        pages = list_pages(request.user)
        allowed_filters(request, "tag")
        return paginated(request, self, with_tags(request, "page", pages), asdict)

    @extend_schema(
        description="Creates a Standalone Page. Item Pages are created with their item.",
        request=PageCreateSerializer,
        responses={201: PageSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = PageCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        page = create_page(request.user, **body.validated_data)
        return Response(asdict(page), status=http_status.HTTP_201_CREATED)


class PageDetailView(APIView):
    @extend_schema(responses={200: PageSerializer, **errors(401, 403, 404)})
    def get(self, request, page_id):
        return Response(asdict(get_page(request.user, page_id)))

    @extend_schema(
        description="An Item Page takes its name from its item, so only its `body` can change.",
        request=PageChangeSerializer,
        responses={200: PageSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, page_id):
        require_editor(request.user)
        body = PageChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_page(request.user, page_id, **body.validated_data)))

    @extend_schema(
        description="Deletes a Standalone Page. An Item Page is deleted with its item.",
        responses={204: None, **errors(400, 401, 403, 404)},
    )
    def delete(self, request, page_id):
        delete_page(request.user, page_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class AnyAcceptNegotiation(BaseContentNegotiation):
    """A download answers with the file's own type, whatever the client says it accepts."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class PageAttachmentListView(APIView):
    parser_classes = [MultiPartParser]

    @extend_schema(
        operation_id="page_attachments_list",
        parameters=PAGINATION,
        responses={200: page_of(AttachmentSerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, page_id):
        attachments = list_attachments(request.user, page_id)
        allowed_filters(request)
        return paginated(request, self, attachments, asdict)

    @extend_schema(
        operation_id="page_attachments_create",
        description="Uploads a file as multipart form data, in the field `file`.",
        request={"multipart/form-data": AttachmentUploadSerializer},
        responses={201: AttachmentSerializer, **errors(400, 401, 403, 404)},
    )
    def post(self, request, page_id):
        require_editor(request.user)
        attachment = add_attachment(request.user, page_id, request.FILES.get("file"))
        return Response(asdict(attachment), status=http_status.HTTP_201_CREATED)


class AttachmentDetailView(APIView):
    @extend_schema(responses={200: AttachmentSerializer, **errors(401, 403, 404)})
    def get(self, request, attachment_id):
        return Response(asdict(get_attachment(request.user, attachment_id)))

    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, attachment_id):
        delete_attachment(request.user, attachment_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class AttachmentDownloadView(APIView):
    content_negotiation_class = AnyAcceptNegotiation

    @extend_schema(
        operation_id="attachment_download",
        description="The file. Only a verified PNG, JPEG, GIF or WebP is served inline.",
        responses={
            (200, "application/octet-stream"): OpenApiTypes.BINARY,
            **errors(401, 403, 404),
        },
    )
    def get(self, request, attachment_id):
        return attachment_response(request.user, attachment_id)


class PageTemplateListView(APIView):
    @extend_schema(
        operation_id="page_templates_list",
        parameters=PAGINATION,
        responses={200: page_of(PageTemplateSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        templates = list_page_templates(request.user)
        allowed_filters(request)
        return paginated(request, self, templates, asdict)

    @extend_schema(
        description="Adds a Device Type's override. Each kind's default already exists.",
        request=PageTemplateCreateSerializer,
        responses={201: PageTemplateSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = PageTemplateCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        template = create_page_template(request.user, **body.validated_data)
        return Response(asdict(template), status=http_status.HTTP_201_CREATED)


class PageTemplateDetailView(APIView):
    @extend_schema(responses={200: PageTemplateSerializer, **errors(401, 403, 404)})
    def get(self, request, template_id):
        return Response(asdict(get_page_template(request.user, template_id)))

    @extend_schema(
        description=(
            "Updates every Page using the template. Removing a Free-text Section that has "
            "content returns `409` until `confirmed_removals` matches the affected Page counts."
        ),
        request=PageTemplateChangeSerializer,
        responses={200: PageTemplateSerializer, **errors(400, 401, 403, 404, 409)},
    )
    def patch(self, request, template_id):
        require_editor(request.user)
        body = PageTemplateChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            template = update_page_template(request.user, template_id, **body.validated_data)
        except UnconfirmedSectionRemoval as needed:
            counts = {entry.name: [str(entry.page_count)] for entry in needed.removed}
            message = _("Removing these Free-text Sections deletes Page content.")
            envelope = {"code": "confirmation_required", "message": message, "fields": counts}
            return Response({"error": envelope}, status=http_status.HTTP_409_CONFLICT)
        return Response(asdict(template))

    @extend_schema(
        description="Deletes a Device Type's override. A default cannot be deleted.",
        responses={204: None, **errors(400, 401, 403, 404)},
    )
    def delete(self, request, template_id):
        delete_page_template(request.user, template_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class PageTemplatePreviewView(APIView):
    @extend_schema(
        operation_id="page_template_preview",
        description="The Free-text Sections this text would remove. Nothing is changed.",
        request=PageTemplatePreviewSerializer,
        responses={200: RemovedSectionSerializer(many=True), **errors(400, 401, 403, 404)},
    )
    def post(self, request, template_id):
        require_editor(request.user)
        body = PageTemplatePreviewSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        removed = preview_template_change(request.user, template_id, body.validated_data["body"])
        return Response([asdict(entry) for entry in removed])


class ItemPageView(APIView):
    @extend_schema(
        operation_id="item_page_retrieve",
        description="The item's Item Page. Customers, Sites, Racks, Devices, VLANs and Subnets have one.",
        responses={200: PageSerializer, **errors(401, 403, 404)},
    )
    def get(self, request, kind, item_id):
        return Response(asdict(get_item_page(request.user, kind, item_id)))


class ItemReferencesView(APIView):
    @extend_schema(
        operation_id="item_references_list",
        description="Every Page that mentions the item.",
        parameters=PAGINATION,
        responses={200: PAGE_SUMMARY_LIST, **errors(400, 401, 403, 404)},
    )
    def get(self, request, kind, item_id):
        pages = referenced_by(request.user, kind, item_id)
        allowed_filters(request)
        return paginated(request, self, pages, asdict)


class PageLiveFieldsView(APIView):
    @extend_schema(
        operation_id="page_live_fields_list",
        description="Every Live Field in the Page with its current value.",
        parameters=PAGINATION,
        responses={200: page_of(LiveFieldValueSerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, page_id):
        values = live_field_values(request.user, page_id)
        allowed_filters(request)
        return paginated(request, self, values, asdict)


class LiveFieldOptionsView(APIView):
    @extend_schema(
        operation_id="live_field_options_list",
        description="Up to 20 item fields matching every word of `q`.",
        parameters=[OpenApiParameter("q", str, description="Words from the item or field name.")],
        responses={200: LiveFieldOptionSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request):
        filters = allowed_filters(request, "q")
        options = live_field_options(request.user, filters.get("q", ""))
        return Response([asdict(option) for option in options])


class MentionOptionsView(APIView):
    @extend_schema(
        operation_id="mention_options_list",
        description="Up to 10 items whose name contains `q`.",
        parameters=[OpenApiParameter("q", str, description="Part of the item's name.")],
        responses={200: MentionOptionSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request):
        filters = allowed_filters(request, "q")
        options = mention_options(request.user, filters.get("q", ""))
        return Response([asdict(option) for option in options])


class TagListView(APIView):
    @extend_schema(
        operation_id="tags_list",
        parameters=PAGINATION,
        responses={200: page_of(TagSerializer), **errors(400, 401, 403)},
    )
    def get(self, request):
        tags = list_tags(request.user)
        allowed_filters(request)
        return paginated(request, self, tags, asdict)

    @extend_schema(
        description="Names are unique in any spelling, so `Prod` and `prod` are one Tag.",
        request=TagChangeSerializer,
        responses={201: TagSerializer, **errors(400, 401, 403)},
    )
    def post(self, request):
        require_editor(request.user)
        body = TagChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        tag = create_tag(request.user, body.validated_data["name"])
        return Response(asdict(tag), status=http_status.HTTP_201_CREATED)


class TagDetailView(APIView):
    @extend_schema(
        description="The Tag and every item carrying it.",
        responses={200: TagUsageSerializer, **errors(401, 403, 404)},
    )
    def get(self, request, tag_id):
        return Response(asdict(tag_usage(request.user, tag_id)))

    @extend_schema(
        request=TagChangeSerializer,
        responses={200: TagSerializer, **errors(400, 401, 403, 404)},
    )
    def patch(self, request, tag_id):
        require_editor(request.user)
        body = TagChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(asdict(update_tag(request.user, tag_id, body.validated_data["name"])))

    @extend_schema(
        description="Removes the Tag from every item, recorded in each item's History.",
        responses={204: None, **errors(401, 403, 404)},
    )
    def delete(self, request, tag_id):
        delete_tag(request.user, tag_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class ItemTagsView(APIView):
    @extend_schema(
        operation_id="item_tags_list",
        parameters=PAGINATION,
        responses={200: page_of(TagRefSerializer), **errors(400, 401, 403, 404)},
    )
    def get(self, request, kind, item_id):
        tags = tags_of(request.user, kind, item_id)
        allowed_filters(request)
        return paginated(request, self, tags, asdict)

    @extend_schema(
        description="Adds a Tag, creating it on first use.",
        request=TagChangeSerializer,
        responses={201: TagRefSerializer, **errors(400, 401, 403, 404)},
    )
    def post(self, request, kind, item_id):
        require_editor(request.user)
        body = TagChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        tag = add_tag(request.user, kind, item_id, body.validated_data["name"])
        return Response(asdict(tag), status=http_status.HTTP_201_CREATED)


class ItemTagDetailView(APIView):
    @extend_schema(responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, kind, item_id, tag_id):
        remove_tag(request.user, kind, item_id, tag_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)
