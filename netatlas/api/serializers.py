"""Request and response shapes. Plain serializers only: models stay behind the services."""

from drf_spectacular.utils import OpenApiResponse, inline_serializer
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
