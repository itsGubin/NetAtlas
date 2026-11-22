"""Request and response shapes. Plain serializers only: models stay behind the services."""

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiResponse, extend_schema_field, inline_serializer
from rest_framework import serializers

from netatlas.accounts.roles import Role

DEVICE_KINDS = ["physical", "virtual"]
INTERFACE_KINDS = ["physical", "virtual", "lag"]


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


class DeviceInterfaceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=INTERFACE_KINDS)


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
    interfaces = DeviceInterfaceSerializer(many=True)


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
