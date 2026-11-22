from django import forms
from django.core.exceptions import ValidationError
from django.http import Http404
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
    INTERFACE_KINDS,
    cable_candidates,
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
    format_interface_lines,
    get_cable,
    get_device,
    get_device_type,
    get_interface,
    get_rack,
    list_cables,
    list_device_types,
    list_devices,
    list_racks,
    pair_candidates,
    pair_interfaces,
    parse_interface_lines,
    place_device,
    rack_elevation,
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
def devices(request):
    if request.method == "GET":
        return _render_devices(request)

    require_editor(request.user)
    try:
        create_device(request.user, **_new_device_values(request))
    except ValidationError as error:
        return _render_devices(request, errors=error.messages, form=request.POST)
    return redirect("web:devices")


@require_http_methods(["GET", "POST"])
def device(request, device_id):
    if request.method == "GET":
        return _render_device(request, device_id)

    require_editor(request.user)
    try:
        update_device(request.user, device_id, **_device_changes(request))
    except ValidationError as error:
        return _render_device(request, device_id, errors=error.messages, form=request.POST)
    return redirect("web:device", device_id=device_id)


@require_POST
def device_remove(request, device_id):
    try:
        delete_device(request.user, device_id)
    except ValidationError as error:
        return _render_device(request, device_id, errors=error.messages)
    return redirect("web:devices")


@require_POST
def device_interfaces(request, device_id):
    require_editor(request.user)
    try:
        create_interface(request.user, device_id, **_interface_values(request))
    except ValidationError as error:
        return _render_device(
            request, device_id, errors=error.messages, interface_form=request.POST
        )
    return redirect("web:device", device_id=device_id)


@require_http_methods(["GET", "POST"])
def interface(request, interface_id):
    if request.method == "GET":
        return _render_interface(request, interface_id)

    require_editor(request.user)
    try:
        update_interface(request.user, interface_id, **_interface_values(request))
    except ValidationError as error:
        return _render_interface(request, interface_id, errors=error.messages, form=request.POST)
    return redirect("web:interface", interface_id=interface_id)


@require_POST
def interface_remove(request, interface_id):
    require_editor(request.user)
    device_id = get_interface(request.user, interface_id).device_id
    delete_interface(request.user, interface_id)
    return redirect("web:device", device_id=device_id)


@require_POST
def interface_cable(request, interface_id):
    require_editor(request.user)
    try:
        peer_id = _required_id(request, "peer_id")
        create_cable(request.user, interface_id, peer_id, request.POST.get("label", ""))
    except ValidationError as error:
        return _render_interface(request, interface_id, errors=error.messages)
    return redirect("web:interface", interface_id=interface_id)


@require_POST
def interface_pair(request, interface_id):
    require_editor(request.user)
    try:
        pair_interfaces(request.user, interface_id, _required_id(request, "partner_id"))
    except ValidationError as error:
        return _render_interface(request, interface_id, errors=error.messages)
    return redirect("web:interface", interface_id=interface_id)


@require_POST
def interface_unpair(request, interface_id):
    unpair_interface(request.user, interface_id)
    return redirect("web:interface", interface_id=interface_id)


def interface_trace(request, interface_id):
    item = get_interface(request.user, interface_id)
    if item.kind != "physical":
        raise Http404
    context = {"interface": item, "trace": trace_interface(request.user, interface_id)}
    return render(request, "netatlas/trace.html", context)


@require_POST
def device_placement(request, device_id):
    require_editor(request.user)
    try:
        rack_id = _required_id(request, "rack_id")
        unit = forms.IntegerField(required=False).clean(request.POST.get("unit", ""))
        face = request.POST.get("face") or None
        place_device(request.user, device_id, rack_id, unit=unit, face=face)
    except ValidationError as error:
        return _render_device(request, device_id, errors=error.messages)
    return redirect("web:device", device_id=device_id)


@require_POST
def device_unrack(request, device_id):
    unrack_device(request.user, device_id)
    return redirect("web:device", device_id=device_id)


@require_http_methods(["GET", "POST"])
def racks(request):
    if request.method == "GET":
        return _render_racks(request)

    require_editor(request.user)
    try:
        create_rack(
            request.user,
            name=request.POST.get("name", ""),
            site_id=_required_id(request, "site_id"),
            height=_rack_height(request),
        )
    except ValidationError as error:
        return _render_racks(request, errors=error.messages, form=request.POST)
    return redirect("web:racks")


@require_http_methods(["GET", "POST"])
def rack(request, rack_id):
    if request.method == "GET":
        return _render_rack(request, rack_id)

    require_editor(request.user)
    try:
        update_rack(
            request.user,
            rack_id,
            name=request.POST.get("name", ""),
            height=_rack_height(request),
        )
    except ValidationError as error:
        return _render_rack(request, rack_id, errors=error.messages, form=request.POST)
    return redirect("web:rack", rack_id=rack_id)


@require_POST
def rack_remove(request, rack_id):
    try:
        delete_rack(request.user, rack_id)
    except ValidationError as error:
        return _render_rack(request, rack_id, errors=error.messages)
    return redirect("web:racks")


def _rack_height(request) -> int:
    try:
        return forms.IntegerField(min_value=1, max_value=100).clean(
            request.POST.get("height", "")
        )
    except ValidationError as error:
        raise ValidationError({"height": error.messages}) from error


def _render_racks(request, errors=(), form=None):
    context = {
        "racks": list_racks(request.user),
        "sites": list_sites(request.user),
        "selected_site": _submitted_id(request, "site_id"),
        "errors": errors,
        "form": form or {"height": 42},
    }
    return render(request, "netatlas/racks.html", context, status=400 if errors else 200)


def _render_rack(request, rack_id, errors=(), form=None):
    item = get_rack(request.user, rack_id)
    context = {
        "rack": item,
        "elevation": rack_elevation(request.user, rack_id),
        "entries": item_history(request.user, "rack", item.id),
        "errors": errors,
        "form": form or {"name": item.name, "height": item.height},
    }
    return render(request, "netatlas/rack.html", context, status=400 if errors else 200)


def cables(request):
    return render(request, "netatlas/cables.html", {"cables": list_cables(request.user)})


@require_http_methods(["GET", "POST"])
def cable(request, cable_id):
    if request.method == "GET":
        return _render_cable(request, cable_id)

    require_editor(request.user)
    label = request.POST.get("label", "")
    try:
        update_cable(request.user, cable_id, label=label)
    except ValidationError as error:
        return _render_cable(request, cable_id, errors=error.messages, label=label)
    return redirect("web:cable", cable_id=cable_id)


@require_POST
def cable_remove(request, cable_id):
    delete_cable(request.user, cable_id)
    return redirect("web:cables")


def _render_cable(request, cable_id, errors=(), label=None):
    item = get_cable(request.user, cable_id)
    context = {
        "cable": item,
        "entries": item_history(request.user, "cable", item.id),
        "errors": errors,
        "label": item.label if label is None else label,
    }
    return render(request, "netatlas/cable.html", context, status=400 if errors else 200)


def _required_id(request, field: str) -> int:
    value = _posted_id(request, field)
    if value is None:
        raise ValidationError({field: [_("Make a choice.")]})
    return value


def _interface_values(request) -> dict:
    values = {"name": request.POST.get("name", ""), "kind": request.POST.get("kind", "")}
    # A Device without LAGs shows no LAG field, so its absence must change nothing.
    if "lag_id" in request.POST:
        values["lag_id"] = _posted_id(request, "lag_id")
    return values


def _interface_kinds(device_kind: str) -> tuple[str, ...]:
    return ("virtual",) if device_kind == "virtual" else INTERFACE_KINDS


def _render_interface(request, interface_id, errors=(), form=None):
    item = get_interface(request.user, interface_id)
    owner = get_device(request.user, item.device_id)
    is_physical = item.kind == "physical"
    context = {
        "interface": item,
        "device": owner,
        "interface_kinds": _interface_kinds(owner.kind),
        "lags": [i for i in owner.interfaces if i.kind == "lag" and i.id != item.id],
        "selected_lag": _submitted_id(request, "lag_id", item.lag_id),
        "interface_form": form or {"name": item.name, "kind": item.kind},
        "is_physical": is_physical,
        "cable_choices": cable_candidates(request.user, item.id)
        if is_physical and not item.cable_id
        else [],
        "pair_choices": pair_candidates(request.user, item.id)
        if is_physical and not item.paired_interface_id
        else [],
        "entries": item_history(request.user, "interface", item.id),
        "errors": errors,
    }
    return render(request, "netatlas/interface.html", context, status=400 if errors else 200)


def _new_device_values(request) -> dict:
    kind = request.POST.get("kind", "")
    values = {
        "name": request.POST.get("name", ""),
        "kind": kind,
        "parent_id": _posted_id(request, "parent_id"),
        "device_type_id": _posted_id(request, "device_type_id"),
    }
    # The form always submits a Site, but a Virtual Device takes its Hypervisor's.
    if kind != "virtual":
        values["site_id"] = _posted_id(request, "site_id")
    return values


def _device_changes(request) -> dict:
    values = {"name": request.POST.get("name", "")}
    for field in ("site_id", "parent_id"):
        if field in request.POST:
            values[field] = _posted_id(request, field)
    if "rack_height" in request.POST:
        try:
            values["rack_height"] = forms.IntegerField(min_value=0, max_value=32767).clean(
                request.POST["rack_height"]
            )
        except ValidationError as error:
            raise ValidationError({"rack_height": error.messages}) from error
    return values


def _posted_id(request, field: str) -> int | None:
    value = request.POST.get(field, "")
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValidationError({field: [_("This item does not exist.")]}) from error


def _submitted_id(request, field: str, default=None):
    """The choice to preselect when a form is shown again after an error."""
    value = request.POST.get(field)
    if value is None:
        return default
    return int(value) if value.isdecimal() else None


def _render_devices(request, errors=(), form=None):
    all_devices = list_devices(request.user)
    context = {
        "devices": all_devices,
        "sites": list_sites(request.user),
        "device_types": list_device_types(request.user),
        "hypervisors": all_devices,
        "selected_site": _submitted_id(request, "site_id"),
        "selected_parent": _submitted_id(request, "parent_id"),
        "selected_type": _submitted_id(request, "device_type_id"),
        "errors": errors,
        "form": form or {},
    }
    return render(request, "netatlas/devices.html", context, status=400 if errors else 200)


def _render_device(request, device_id, errors=(), form=None, interface_form=None):
    item = get_device(request.user, device_id)
    nested = virtual_devices(request.user, device_id)
    # A Device cannot run on itself or on anything nested on it.
    excluded = {item.id, *(entry.id for entry in nested)}
    context = {
        "device": item,
        "virtual_devices": nested,
        "sites": list_sites(request.user),
        "hypervisors": [d for d in list_devices(request.user) if d.id not in excluded],
        "selected_site": _submitted_id(request, "site_id", item.site_id),
        "selected_parent": _submitted_id(request, "parent_id", item.parent_id),
        "entries": item_history(request.user, "device", item.id),
        "errors": errors,
        "form": {"name": item.name, "rack_height": item.rack_height}
        if interface_form or not form
        else form,
        "interface_form": interface_form or {},
        "interface_kinds": _interface_kinds(item.kind),
        "lags": [i for i in item.interfaces if i.kind == "lag"],
        "selected_lag": _submitted_id(request, "lag_id"),
        "racks": list_racks(request.user, site_id=item.site_id) if item.kind == "physical" else [],
        "selected_rack": _submitted_id(request, "rack_id", item.rack_id),
        "placement": {
            "unit": request.POST.get("unit", item.rack_unit or ""),
            "face": request.POST.get("face", item.rack_face),
        },
    }
    return render(request, "netatlas/device.html", context, status=400 if errors else 200)


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
        "selected_customer": _submitted_id(request, "customer_id"),
        "errors": errors,
        "name": name,
    }
    return render(request, "netatlas/sites.html", context, status=400 if errors else 200)


def _render_site(request, site_id, errors=(), name=None):
    item = get_site(request.user, site_id)
    context = {
        "site": item,
        "customers": list_customers(request.user),
        "selected_customer": _submitted_id(request, "customer_id", item.customer_id),
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
