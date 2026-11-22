from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

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
    revoke_token,
    role_of,
)
from netatlas.devices.services import (
    create_device_type,
    delete_device_type,
    format_interface_lines,
    get_device_type,
    list_device_types,
    parse_interface_lines,
    update_device_type,
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


def home(request):
    return render(request, "netatlas/home.html")


def status(request):
    return render(request, "netatlas/status.html", {"status": get_system_status()})


@require_http_methods(["GET", "POST"])
def users(request):
    require_admin(request.user)
    if request.method == "GET":
        return _render_users(request)

    username = request.POST.get("username", "")
    role = request.POST.get("role", "")
    try:
        create_user(request.user, username, request.POST.get("password", ""), role)
    except ValidationError as error:
        return _render_users(request, errors=error.messages, username=username, new_role=role)
    return redirect("web:users")


@require_POST
def user_role(request, user_id):
    user = get_user(request.user, user_id)
    try:
        assign_role(request.user, user, request.POST.get("role", ""))
    except ValidationError as error:
        return _render_users(request, errors=error.messages)
    return redirect("web:users")


@require_http_methods(["GET", "POST"])
def tokens(request):
    if request.method == "GET":
        return _render_tokens(request)

    name = request.POST.get("name", "")
    try:
        expires_at = forms.DateTimeField(required=False).clean(request.POST.get("expires_at", ""))
        token = create_token(request.user, name, expires_at=expires_at)
    except ValidationError as error:
        return _render_tokens(request, errors=error.messages, name=name)

    response = _render_tokens(request, secret=token.secret)
    # The page holds the only copy of the secret, so nothing may keep it.
    patch_cache_control(response, no_store=True)
    return response


@require_POST
def token_revoke(request, token_id):
    revoke_token(request.user, token_id)
    return redirect("web:tokens")


def history(request, kind, item_id):
    entries = item_history(request.user, kind, item_id)
    context = {"entries": entries, "item_label": entries[0].item_label}
    return render(request, "netatlas/history.html", context)


@require_http_methods(["GET", "POST"])
def customers(request):
    if request.method == "GET":
        return _render_customers(request)

    require_editor(request.user)
    name = request.POST.get("name", "")
    try:
        create_customer(request.user, name=name)
    except ValidationError as error:
        return _render_customers(request, errors=error.messages, name=name)
    return redirect("web:customers")


@require_http_methods(["GET", "POST"])
def customer(request, customer_id):
    if request.method == "GET":
        return _render_customer(request, customer_id)

    require_editor(request.user)
    name = request.POST.get("name", "")
    try:
        update_customer(request.user, customer_id, name=name)
    except ValidationError as error:
        return _render_customer(request, customer_id, errors=error.messages, name=name)
    return redirect("web:customer", customer_id=customer_id)


@require_POST
def customer_remove(request, customer_id):
    delete_customer(request.user, customer_id)
    return redirect("web:sites")


@require_http_methods(["GET", "POST"])
def sites(request):
    if request.method == "GET":
        return _render_sites(request)

    require_editor(request.user)
    name = request.POST.get("name", "")
    try:
        create_site(request.user, name=name, **_customer_choice(request))
    except ValidationError as error:
        return _render_sites(request, errors=error.messages, name=name)
    return redirect("web:sites")


@require_http_methods(["GET", "POST"])
def site(request, site_id):
    if request.method == "GET":
        return _render_site(request, site_id)

    require_editor(request.user)
    name = request.POST.get("name", "")
    try:
        update_site(request.user, site_id, name=name, **_customer_choice(request))
    except ValidationError as error:
        return _render_site(request, site_id, errors=error.messages, name=name)
    return redirect("web:site", site_id=site_id)


@require_POST
def site_remove(request, site_id):
    try:
        delete_site(request.user, site_id)
    except ValidationError as error:
        return _render_site(request, site_id, errors=error.messages)
    return redirect("web:sites")


@require_http_methods(["GET", "POST"])
def device_types(request):
    if request.method == "GET":
        return _render_device_types(request)

    require_editor(request.user)
    try:
        create_device_type(request.user, **_device_type_values(request))
    except ValidationError as error:
        return _render_device_types(request, errors=error.messages, form=request.POST)
    return redirect("web:device-types")


@require_http_methods(["GET", "POST"])
def device_type(request, device_type_id):
    if request.method == "GET":
        return _render_device_type(request, device_type_id)

    require_editor(request.user)
    try:
        update_device_type(request.user, device_type_id, **_device_type_values(request))
    except ValidationError as error:
        return _render_device_type(
            request, device_type_id, errors=error.messages, form=request.POST
        )
    return redirect("web:device-type", device_type_id=device_type_id)


@require_POST
def device_type_remove(request, device_type_id):
    delete_device_type(request.user, device_type_id)
    return redirect("web:device-types")


def _device_type_values(request) -> dict:
    try:
        rack_height = forms.IntegerField(min_value=0, max_value=32767).clean(
            request.POST.get("rack_height", "")
        )
    except ValidationError as error:
        raise ValidationError({"rack_height": error.messages}) from error
    return {
        "make": request.POST.get("make", ""),
        "model_name": request.POST.get("model_name", ""),
        "rack_height": rack_height,
        "interfaces": parse_interface_lines(request.POST.get("interfaces", "")),
    }


def _render_device_types(request, errors=(), form=None):
    context = {
        "device_types": list_device_types(request.user),
        "errors": errors,
        "form": form or {"rack_height": 1},
    }
    return render(request, "netatlas/device_types.html", context, status=400 if errors else 200)


def _render_device_type(request, device_type_id, errors=(), form=None):
    item = get_device_type(request.user, device_type_id)
    context = {
        "device_type": item,
        "entries": item_history(request.user, "device-type", item.id),
        "errors": errors,
        "form": form
        or {
            "make": item.make,
            "model_name": item.model_name,
            "rack_height": item.rack_height,
            "interfaces": format_interface_lines(item.interfaces),
        },
    }
    return render(request, "netatlas/device_type.html", context, status=400 if errors else 200)


def _customer_choice(request) -> dict:
    """The submitted Customer. The field is absent while no Customers exist."""
    if "customer_id" not in request.POST:
        return {}
    value = request.POST["customer_id"]
    if not value:
        return {"customer_id": None}
    try:
        return {"customer_id": int(value)}
    except ValueError as error:
        raise ValidationError({"customer": [_("Unknown Customer.")]}) from error


def _submitted_customer(request, default=None):
    """The Customer to preselect when a form is shown again after an error."""
    value = request.POST.get("customer_id")
    if value is None:
        return default
    return int(value) if value.isdecimal() else None


def _render_customers(request, errors=(), name=""):
    context = {"customers": list_customers(request.user), "errors": errors, "name": name}
    return render(request, "netatlas/customers.html", context, status=400 if errors else 200)


def _render_customer(request, customer_id, errors=(), name=None):
    item = get_customer(request.user, customer_id)
    context = {
        "customer": item,
        "entries": item_history(request.user, "customer", item.id),
        "errors": errors,
        "name": item.name if name is None else name,
    }
    return render(request, "netatlas/customer.html", context, status=400 if errors else 200)


def _render_sites(request, errors=(), name=""):
    context = {
        "sites": list_sites(request.user),
        "customers": list_customers(request.user),
        "selected_customer": _submitted_customer(request),
        "errors": errors,
        "name": name,
    }
    return render(request, "netatlas/sites.html", context, status=400 if errors else 200)


def _render_site(request, site_id, errors=(), name=None):
    item = get_site(request.user, site_id)
    context = {
        "site": item,
        "customers": list_customers(request.user),
        "selected_customer": _submitted_customer(request, item.customer_id),
        "entries": item_history(request.user, "site", item.id),
        "errors": errors,
        "name": item.name if name is None else name,
    }
    return render(request, "netatlas/site.html", context, status=400 if errors else 200)


def _render_tokens(request, errors=(), name="", secret=None):
    is_admin = role_of(request.user) == Role.ADMIN
    context = {
        "tokens": list_all_tokens(request.user) if is_admin else list_tokens(request.user),
        "show_owner": is_admin,
        "errors": errors,
        "name": name,
        "secret": secret,
    }
    status = 400 if errors else 201 if secret else 200
    return render(request, "netatlas/tokens.html", context, status=status)


def _render_users(request, errors=(), username="", new_role=Role.VIEWER):
    context = {
        "users": list_users(request.user),
        "roles": Role.choices,
        "errors": errors,
        "username": username,
        "new_role": new_role,
    }
    return render(request, "netatlas/users.html", context, status=400 if errors else 200)
