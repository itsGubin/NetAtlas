"""Request and response shapes. Plain serializers only: models stay behind the services."""

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiResponse, extend_schema_field, inline_serializer
from rest_framework import serializers

from netatlas.accounts.roles import Role

DEVICE_KINDS = ["physical", "virtual"]
INTERFACE_KINDS = ["physical", "virtual", "lag"]
IP_ADDRESS_STATUSES = ["assigned", "reserved"]


class StatusSerializer(serializers.Serializer):
    version = serializers.CharField()
    database = serializers.ChoiceField(choices=["up", "down"])


class UserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    role = serializers.ChoiceField(choices=Role.choices, allow_null=True)


class NewUserSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    role = serializers.ChoiceField(choices=Role.choices)


class RoleChangeSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)


class TokenSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.CharField()
    prefix = serializers.CharField()
    created_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField(allow_null=True)
    revoked_at = serializers.DateTimeField(allow_null=True)


class CreatedTokenSerializer(TokenSerializer):
    secret = serializers.CharField(help_text="Shown only once. NetAtlas stores just a hash.")


class NewTokenSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class FieldChangeSerializer(serializers.Serializer):
    field = serializers.CharField()
    old = serializers.JSONField(allow_null=True)
    new = serializers.JSONField(allow_null=True)


class HistoryEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    item_kind = serializers.CharField()
    item_id = serializers.IntegerField()
    item_label = serializers.CharField()
    action = serializers.ChoiceField(choices=["created", "updated", "deleted"])
    actor = serializers.CharField()
    at = serializers.DateTimeField()
    changes = FieldChangeSerializer(many=True)


class CustomerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class CustomerChangeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)


class SiteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    customer_id = serializers.IntegerField(allow_null=True)
    customer_name = serializers.CharField(allow_null=True)


class SiteChangeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    customer_id = serializers.IntegerField(required=False, allow_null=True)


class NewSiteSerializer(SiteChangeSerializer):
    name = serializers.CharField(max_length=100)


class SiteFilterSerializer(serializers.Serializer):
    customer = serializers.IntegerField(required=False)


class DeviceTypeInterfaceSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="On input, may be a bulk pattern such as `port[1-48]`.")
    kind = serializers.ChoiceField(choices=INTERFACE_KINDS)


@extend_schema_field(DeviceTypeInterfaceSerializer(many=True))
class InterfaceListField(serializers.JSONField):
    """Passed to the service as is, so its errors keep the standard field-error shape."""


class DeviceTypeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    make = serializers.CharField()
    model_name = serializers.CharField()
    rack_height = serializers.IntegerField()
    interfaces = DeviceTypeInterfaceSerializer(many=True)


class DeviceTypeChangeSerializer(serializers.Serializer):
    make = serializers.CharField(max_length=100, required=False)
    model_name = serializers.CharField(max_length=100, required=False)
    rack_height = serializers.IntegerField(min_value=0, max_value=32767, required=False)
    interfaces = InterfaceListField(required=False)


class NewDeviceTypeSerializer(DeviceTypeChangeSerializer):
    make = serializers.CharField(max_length=100)
    model_name = serializers.CharField(max_length=100)


class IpAddressRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    address = serializers.CharField()


class VlanRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)


class InterfaceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    device_id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=INTERFACE_KINDS)
    lag_id = serializers.IntegerField(allow_null=True)
    lag_name = serializers.CharField(allow_null=True)
    cable_id = serializers.IntegerField(allow_null=True)
    peer_interface_id = serializers.IntegerField(allow_null=True)
    peer_interface_name = serializers.CharField(allow_null=True)
    peer_device_id = serializers.IntegerField(allow_null=True)
    peer_device_name = serializers.CharField(allow_null=True)
    paired_interface_id = serializers.IntegerField(allow_null=True)
    paired_interface_name = serializers.CharField(allow_null=True)
    untagged_vlan = VlanRefSerializer(allow_null=True)
    tagged_vlans = VlanRefSerializer(many=True)
    ip_addresses = IpAddressRefSerializer(many=True)


class NewInterfaceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)
    kind = serializers.ChoiceField(choices=INTERFACE_KINDS)
    lag_id = serializers.IntegerField(required=False, allow_null=True)


class InterfaceChangeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64, required=False)
    kind = serializers.ChoiceField(choices=INTERFACE_KINDS, required=False)
    lag_id = serializers.IntegerField(required=False, allow_null=True)


class DeviceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=DEVICE_KINDS)
    site_id = serializers.IntegerField()
    site_name = serializers.CharField()
    parent_id = serializers.IntegerField(allow_null=True)
    parent_name = serializers.CharField(allow_null=True)
    device_type_id = serializers.IntegerField(allow_null=True)
    device_type_name = serializers.CharField(allow_null=True)
    rack_height = serializers.IntegerField()
    rack_id = serializers.IntegerField(allow_null=True)
    rack_name = serializers.CharField(allow_null=True)
    rack_unit = serializers.IntegerField(allow_null=True, help_text="The Device's lowest unit.")
    rack_face = serializers.CharField(allow_blank=True, help_text="`front`, `rear` or empty.")
    interfaces = InterfaceSerializer(many=True)


class NewDeviceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    kind = serializers.ChoiceField(choices=DEVICE_KINDS)
    site_id = serializers.IntegerField(
        required=False, help_text="Required for a Physical Device. Virtual Devices inherit it."
    )
    parent_id = serializers.IntegerField(
        required=False, allow_null=True, help_text="The Hypervisor of a Virtual Device."
    )
    device_type_id = serializers.IntegerField(required=False, allow_null=True)
    rack_height = serializers.IntegerField(min_value=0, max_value=32767, required=False)


class DeviceChangeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    site_id = serializers.IntegerField(required=False)
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    rack_height = serializers.IntegerField(min_value=0, max_value=32767, required=False)

    def validate(self, attrs):
        if "kind" in self.initial_data:
            raise serializers.ValidationError({"kind": [_("A Device's kind cannot be changed.")]})
        return attrs


class DeviceFilterSerializer(serializers.Serializer):
    site = serializers.IntegerField(required=False)
    kind = serializers.ChoiceField(choices=DEVICE_KINDS, required=False)
    parent = serializers.IntegerField(required=False)


class VirtualDeviceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    parent_id = serializers.IntegerField()
    depth = serializers.IntegerField(help_text="1 runs directly on the requested Device.")


class CableSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    label = serializers.CharField(allow_blank=True)
    interface_a_id = serializers.IntegerField()
    interface_a_name = serializers.CharField()
    device_a_id = serializers.IntegerField()
    device_a_name = serializers.CharField()
    interface_b_id = serializers.IntegerField()
    interface_b_name = serializers.CharField()
    device_b_id = serializers.IntegerField()
    device_b_name = serializers.CharField()


class NewCableSerializer(serializers.Serializer):
    interface_a_id = serializers.IntegerField()
    interface_b_id = serializers.IntegerField()
    label = serializers.CharField(max_length=100, required=False, allow_blank=True)


class CableChangeSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=100, required=False, allow_blank=True)


class CableFilterSerializer(serializers.Serializer):
    device = serializers.IntegerField(required=False)


class InterfacePairSerializer(serializers.Serializer):
    interface_id = serializers.IntegerField(help_text="The other Interface of the pair.")


class TraceHopSerializer(serializers.Serializer):
    interface_id = serializers.IntegerField()
    interface_name = serializers.CharField()
    device_id = serializers.IntegerField()
    device_name = serializers.CharField()
    site_name = serializers.CharField()
    is_start = serializers.BooleanField()
    cable_id = serializers.IntegerField(
        allow_null=True, help_text="The Cable to the next hop. Null across a front/rear pair."
    )
    cable_label = serializers.CharField(allow_null=True, allow_blank=True)


class CableTraceSerializer(serializers.Serializer):
    hops = TraceHopSerializer(many=True)
    is_loop = serializers.BooleanField()


class RackSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    site_id = serializers.IntegerField()
    site_name = serializers.CharField()
    height = serializers.IntegerField()


class NewRackSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    site_id = serializers.IntegerField()
    height = serializers.IntegerField(min_value=1, max_value=100, required=False)


class RackChangeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    height = serializers.IntegerField(min_value=1, max_value=100, required=False)


class RackFilterSerializer(serializers.Serializer):
    site = serializers.IntegerField(required=False)


class DevicePlacementSerializer(serializers.Serializer):
    rack_id = serializers.IntegerField()
    unit = serializers.IntegerField(
        required=False, allow_null=True, help_text="Lowest unit. Omit for a zero-height Device."
    )
    face = serializers.ChoiceField(
        choices=["front", "rear"],
        required=False,
        allow_null=True,
        help_text="Omit for a zero-height Device.",
    )


class ElevationUnitSerializer(serializers.Serializer):
    unit = serializers.IntegerField()
    device_id = serializers.IntegerField(allow_null=True)
    device_name = serializers.CharField(allow_null=True)
    span = serializers.IntegerField()
    is_covered = serializers.BooleanField(
        help_text="True for the lower units of a taller Device."
    )


class RackedDeviceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class RackElevationSerializer(serializers.Serializer):
    rack = RackSerializer()
    front = ElevationUnitSerializer(many=True)
    rear = ElevationUnitSerializer(many=True)
    zero_height = RackedDeviceSerializer(many=True)


class SubnetSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    cidr = serializers.CharField()
    version = serializers.IntegerField()
    prefix_length = serializers.IntegerField()
    site_id = serializers.IntegerField()
    site_name = serializers.CharField()
    parent_id = serializers.IntegerField(
        allow_null=True, help_text="The most specific Subnet containing this one."
    )
    parent_cidr = serializers.CharField(allow_null=True)
    depth = serializers.IntegerField(help_text="0 for a Subnet with no parent.")
    utilisation = serializers.FloatField(help_text="Percent, rounded down to one decimal.")
    vlan_id = serializers.IntegerField(allow_null=True)
    vlan_number = serializers.IntegerField(allow_null=True)
    vlan_name = serializers.CharField(allow_null=True, allow_blank=True)


class NewSubnetSerializer(serializers.Serializer):
    cidr = serializers.CharField(max_length=64)
    site_id = serializers.IntegerField()
    vlan_id = serializers.IntegerField(required=False, allow_null=True)


class SubnetChangeSerializer(serializers.Serializer):
    cidr = serializers.CharField(max_length=64, required=False)
    site_id = serializers.IntegerField(required=False)
    vlan_id = serializers.IntegerField(required=False, allow_null=True)


class SubnetFilterSerializer(serializers.Serializer):
    site = serializers.IntegerField(required=False)
    version = serializers.ChoiceField(choices=[4, 6], required=False)


class VlanSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    site_id = serializers.IntegerField()
    site_name = serializers.CharField()


class NewVlanSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1, max_value=4094)
    site_id = serializers.IntegerField()
    name = serializers.CharField(max_length=100, required=False, allow_blank=True)


class VlanChangeSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1, max_value=4094, required=False)
    name = serializers.CharField(max_length=100, required=False, allow_blank=True)


class VlanFilterSerializer(serializers.Serializer):
    site = serializers.IntegerField(required=False)


class VlanSubnetSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    cidr = serializers.CharField()


class VlanInterfaceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    interface_name = serializers.CharField()
    device_id = serializers.IntegerField()
    device_name = serializers.CharField()
    mode = serializers.ChoiceField(choices=["untagged", "tagged"])


class VlanUsageSerializer(serializers.Serializer):
    vlan = VlanSerializer()
    subnets = VlanSubnetSerializer(many=True)
    interfaces = VlanInterfaceSerializer(many=True)


class IdListField(serializers.ListField):
    """A list of IDs. A bad entry is one message, keeping the standard field-error shape."""

    child = serializers.IntegerField()

    def run_child_validation(self, data):
        try:
            return super().run_child_validation(data)
        except serializers.ValidationError as error:
            raise serializers.ValidationError([_("Must be a list of IDs.")]) from error


class InterfaceVlansSerializer(serializers.Serializer):
    untagged_vlan_id = serializers.IntegerField(allow_null=True)
    tagged_vlan_ids = IdListField(max_length=4094)


class IpAddressSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    address = serializers.CharField()
    version = serializers.IntegerField()
    status = serializers.ChoiceField(choices=IP_ADDRESS_STATUSES)
    site_id = serializers.IntegerField()
    site_name = serializers.CharField()
    subnet_id = serializers.IntegerField(
        allow_null=True, help_text="The most specific Subnet containing this address."
    )
    subnet_cidr = serializers.CharField(allow_null=True)
    interface_id = serializers.IntegerField(allow_null=True)
    interface_name = serializers.CharField(allow_null=True)
    device_id = serializers.IntegerField(allow_null=True)
    device_name = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_blank=True)


class NewIpAddressSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=64)
    site_id = serializers.IntegerField()
    interface_id = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


class IpAddressChangeSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=64, required=False)
    interface_id = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


class AssignIpAddressSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=64)


class IpAddressFilterSerializer(serializers.Serializer):
    site = serializers.IntegerField(required=False)
    subnet = serializers.IntegerField(required=False)
    interface = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(choices=IP_ADDRESS_STATUSES, required=False)


class DhcpRangeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    subnet_id = serializers.IntegerField()
    subnet_cidr = serializers.CharField()
    start = serializers.CharField()
    end = serializers.CharField()
    size = serializers.CharField(help_text="Text, as IPv6 sizes do not fit a JSON number.")
    description = serializers.CharField(allow_blank=True)


class NewDhcpRangeSerializer(serializers.Serializer):
    subnet_id = serializers.IntegerField()
    start = serializers.CharField(max_length=64)
    end = serializers.CharField(max_length=64)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


class DhcpRangeChangeSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=64, required=False)
    end = serializers.CharField(max_length=64, required=False)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


class DhcpRangeFilterSerializer(serializers.Serializer):
    subnet = serializers.IntegerField(required=False)


class SubnetUsageSerializer(serializers.Serializer):
    subnet = SubnetSerializer()
    assigned = serializers.IntegerField()
    reserved = serializers.IntegerField()
    dhcp = serializers.CharField(help_text="Text, as IPv6 counts do not fit a JSON number.")
    nested = serializers.CharField(help_text="Text, as IPv6 counts do not fit a JSON number.")
    free = serializers.CharField(help_text="Text, as IPv6 counts do not fit a JSON number.")
    next_free = serializers.CharField(allow_null=True)
    ip_addresses = IpAddressSerializer(many=True)
    dhcp_ranges = DhcpRangeSerializer(many=True)


class DeleteImpactSerializer(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.IntegerField(allow_null=True)
    label = serializers.CharField()
    effect = serializers.CharField()


class DeletePreviewSerializer(serializers.Serializer):
    kind = serializers.CharField()
    item_id = serializers.IntegerField()
    item_label = serializers.CharField()
    deleted = DeleteImpactSerializer(many=True)
    changed = DeleteImpactSerializer(many=True)
    moved = DeleteImpactSerializer(many=True)
    broken_live_fields = DeleteImpactSerializer(
        many=True, help_text="Live Fields on remaining Pages that will show as broken."
    )
    broken_embeds = DeleteImpactSerializer(
        many=True, help_text="Embedded attachments on remaining Pages that will show as deleted."
    )
    blocked = serializers.ListField(
        child=serializers.CharField(), help_text="Why the delete would be refused."
    )
    fingerprint = serializers.CharField(
        allow_blank=True, help_text="Identifies this exact impact. Empty if blocked."
    )


class TagRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class TagSerializer(TagRefSerializer):
    usage = serializers.IntegerField(help_text="How many items carry this Tag.")


class TagChangeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)


class TaggedItemSerializer(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.IntegerField()
    label = serializers.CharField()


class TagUsageSerializer(serializers.Serializer):
    tag = TagSerializer()
    items = TaggedItemSerializer(many=True)


class PageSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(help_text="An Item Page shows its item's current name.")
    item_kind = serializers.CharField(allow_null=True, help_text="Null on a Standalone Page.")
    item_id = serializers.IntegerField(allow_null=True, help_text="Null on a Standalone Page.")


class PageSerializer(PageSummarySerializer):
    body = serializers.CharField(
        allow_blank=True, help_text="Markdown. A Mention is written `@[device:12]`."
    )
    sections = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        help_text="An Item Page's Free-text Sections by name. Empty on a Standalone Page.",
    )
    template_id = serializers.IntegerField(
        allow_null=True, help_text="The Page Template in use. Null on a Standalone Page."
    )


class PageCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    body = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)


class PageChangeSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, max_length=200)
    body = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    sections = serializers.DictField(
        required=False,
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        help_text="Free-text Sections to change on an Item Page, by name.",
    )


class ImportRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["device", "interface", "subnet", "ip-address", "vlan"])
    csv = serializers.CharField(trim_whitespace=False, help_text="The file's text, with a header.")
    mapping = serializers.DictField(
        required=False,
        child=serializers.CharField(),
        help_text="Each field's column. Without it, columns are matched by name.",
    )


class ImportRowSerializer(serializers.Serializer):
    number = serializers.IntegerField(help_text="As a spreadsheet numbers it. The header is 1.")
    values = serializers.DictField(child=serializers.CharField(allow_blank=True))
    label = serializers.CharField(allow_blank=True)
    errors = serializers.ListField(child=serializers.CharField())


class ImportResultSerializer(serializers.Serializer):
    kind = serializers.CharField()
    total = serializers.IntegerField()
    valid = serializers.IntegerField()
    rows = ImportRowSerializer(many=True)


class SearchHitSerializer(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.IntegerField()
    label = serializers.CharField()
    detail = serializers.CharField(allow_blank=True, help_text="Where the item is.")
    excerpt = serializers.CharField(allow_blank=True, help_text="The matching Page text.")


class SearchGroupSerializer(serializers.Serializer):
    kind = serializers.CharField()
    count = serializers.IntegerField(help_text="Every match of this kind.")
    hits = SearchHitSerializer(many=True, help_text="The best 20.")


class SearchResultsSerializer(serializers.Serializer):
    query = serializers.CharField(allow_blank=True)
    total = serializers.IntegerField()
    groups = SearchGroupSerializer(many=True)


class PagePreviewRequestSerializer(serializers.Serializer):
    page_id = serializers.IntegerField(
        required=False, min_value=1, help_text="The Page being edited. Omit it for a new Page."
    )
    body = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    sections = serializers.DictField(
        required=False,
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        help_text="Free-text Sections of an Item Page, by name.",
    )


class PagePreviewSerializer(serializers.Serializer):
    html = serializers.CharField(allow_blank=True, help_text="Safe HTML. Raw HTML is escaped.")


class AttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    page_id = serializers.IntegerField()
    filename = serializers.CharField()
    content_type = serializers.CharField(help_text="Decided from the file's bytes.")
    size = serializers.IntegerField(help_text="In bytes.")
    is_image = serializers.BooleanField()
    markdown = serializers.CharField(
        allow_blank=True, help_text="Paste into a Page to show this image. Empty for other files."
    )


class AttachmentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class PageTemplateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    item_kind = serializers.CharField()
    device_type_id = serializers.IntegerField(allow_null=True)
    device_type_name = serializers.CharField(allow_null=True)
    is_default = serializers.BooleanField()
    body = serializers.CharField(allow_blank=True)
    sections = serializers.ListField(child=serializers.CharField())
    page_count = serializers.IntegerField()


class PageTemplateCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    device_type_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    body = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)


class PageTemplateChangeSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=100)
    body = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    confirmed_removals = serializers.DictField(
        required=False,
        child=serializers.IntegerField(min_value=0),
        help_text="Each removed Free-text Section with the affected Page count from the preview.",
    )


class PageTemplatePreviewSerializer(serializers.Serializer):
    body = serializers.CharField(allow_blank=True, trim_whitespace=False)


class RemovedSectionSerializer(serializers.Serializer):
    name = serializers.CharField()
    page_count = serializers.IntegerField()


class MentionOptionSerializer(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.IntegerField()
    label = serializers.CharField()
    token = serializers.CharField(help_text="The text an editor inserts for this Mention.")


class LiveFieldOptionSerializer(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.IntegerField()
    field = serializers.CharField()
    label = serializers.CharField()
    token = serializers.CharField(help_text="The text an editor inserts for this Live Field.")


class LiveFieldValueSerializer(serializers.Serializer):
    kind = serializers.CharField()
    item_id = serializers.IntegerField()
    field = serializers.CharField()
    field_label = serializers.CharField()
    item_label = serializers.CharField(allow_null=True, help_text="Null if the item is deleted.")
    values = serializers.ListField(child=serializers.CharField())
    is_broken = serializers.BooleanField(help_text="True if the item is deleted.")


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class ErrorSerializer(serializers.Serializer):
    error = ErrorBodySerializer()


def page_of(item):
    """The documented shape of a paginated list of `item`."""
    name = item.__name__.removesuffix("Serializer")
    return inline_serializer(
        name=f"Paginated{name}List",
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": item(many=True),
        },
    )


def errors(*statuses):
    """The documented error responses of an endpoint."""
    return {status: OpenApiResponse(ErrorSerializer) for status in statuses}
