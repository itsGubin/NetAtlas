"""Conventions shared by every API endpoint. Documented in docs/api.md."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from rest_framework.exceptions import ParseError
from rest_framework.exceptions import ValidationError as ApiValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from netatlas.system.services import is_database_outage
from netatlas.tagging.services import filter_by_tags

PAGINATION_PARAMS = {"page", "page_size"}
ERROR_CODES = {
    400: "validation_error",
    401: "not_authenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    415: "unsupported_media_type",
    429: "throttled",
}


class StandardPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def paginated(request, view, items, serialize):
    """A paginated response of `items`, each passed through `serialize`."""
    paginator = StandardPagination()
    page = paginator.paginate_queryset(items, request, view=view)
    return paginator.get_paginated_response([serialize(item) for item in page])


def allowed_filters(request, *names):
    """The request's filters. An unknown one is an error, so a typo never returns everything."""
    filters = {key: value for key, value in request.query_params.items()}
    unknown = set(filters) - set(names) - PAGINATION_PARAMS
    if unknown:
        raise ApiValidationError({name: [_("Unknown filter.")] for name in sorted(unknown)})
    return {name: filters[name] for name in names if name in filters}


def with_tags(request, kind, items):
    """The items carrying every `tag` in the query. A blank Tag is an error, like a typo."""
    names = request.query_params.getlist("tag")
    if not names:
        return items
    if not all(name.strip() for name in names):
        raise ApiValidationError({"tag": [_("Enter a Tag name.")]})
    return filter_by_tags(kind, items, names)


def exception_handler(error, context):
    """Every API error has the shape `{"error": {"code", "message", "fields"}}`."""
    if is_database_outage(error):
        message = _("The database cannot be reached.")
        return Response(_envelope("unavailable", message, {}), status=503)

    if isinstance(error, ValidationError):
        detail = error.message_dict if hasattr(error, "error_dict") else error.messages
        error = ApiValidationError(detail)

    response = drf_exception_handler(error, context)
    if response is None:
        return None

    code = "parse_error" if isinstance(error, ParseError) else ERROR_CODES.get(
        response.status_code, "error"
    )
    if isinstance(error, ApiValidationError):
        fields = response.data if isinstance(response.data, dict) else {}
        message = _("Invalid input.") if fields else " ".join(str(m) for m in response.data)
    else:
        fields = {}
        message = str(response.data.get("detail", ""))
    response.data = _envelope(code, message, fields)
    return response


def _envelope(code, message, fields):
    return {"error": {"code": code, "message": message, "fields": fields}}
