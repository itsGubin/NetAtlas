from dataclasses import asdict

from django.core.exceptions import ValidationError
from rest_framework import serializers, status as http_status
from rest_framework.exceptions import ValidationError as ApiValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from netatlas.accounts.services import (
    assign_role,
    create_user,
    get_user,
    list_users,
    require_admin,
    summarize,
)
from netatlas.system.services import get_system_status


class StatusView(APIView):
    """Instance health, used by the Compose healthcheck. Public, and touches no session."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        status = get_system_status()
        return Response(asdict(status), status=200 if status.is_healthy else 503)


class NewUserSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    role = serializers.CharField()


class RoleChangeSerializer(serializers.Serializer):
    role = serializers.CharField()


class UserListView(APIView):
    def get(self, request):
        return Response([asdict(user) for user in list_users(request.user)])

    def post(self, request):
        # Check the Role first, so a non-Admin learns nothing from validation errors.
        require_admin(request.user)
        body = NewUserSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            user = create_user(request.user, **body.validated_data)
        except ValidationError as error:
            raise ApiValidationError(error.message_dict) from error
        return Response(asdict(summarize(user)), status=http_status.HTTP_201_CREATED)


class UserDetailView(APIView):
    def patch(self, request, user_id):
        user = get_user(request.user, user_id)
        body = RoleChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            assign_role(request.user, user, body.validated_data["role"])
        except ValidationError as error:
            raise ApiValidationError(error.message_dict) from error
        return Response(asdict(summarize(user)))
