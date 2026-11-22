"""Request and response shapes. Plain serializers only: models stay behind the services."""

from drf_spectacular.utils import OpenApiResponse, extend_schema_field, inline_serializer
from rest_framework import serializers

from netatlas.accounts.roles import Role


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
    kind = serializers.ChoiceField(choices=["physical", "virtual", "lag"])


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
