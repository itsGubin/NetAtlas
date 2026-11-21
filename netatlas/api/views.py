from dataclasses import asdict

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from netatlas.system.services import get_system_status


class StatusView(APIView):
    """Instance health, used by the Compose healthcheck. Public, and touches no session."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        status = get_system_status()
        return Response(asdict(status), status=200 if status.is_healthy else 503)
