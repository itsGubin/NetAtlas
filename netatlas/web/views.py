import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import login
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_http_methods, require_POST

from netatlas.accounts.oidc import OidcLoginFailed, begin_login, finish_login
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
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    create_vlan,
    get_dhcp_range,
    get_ip_address,
    get_subnet,
    get_vlan,
    list_ip_addresses,
    list_subnets,
    list_vlans,
    reserve_ip_address,
    subnet_usage,
    unassign_ip_address,
    update_dhcp_range,
    update_ip_address,
    update_subnet,
    update_vlan,
    vlan_usage,
)
from netatlas.deletion.services import StaleDelete, confirm_delete, preview_delete
from netatlas.devices.services import (
    INTERFACE_KINDS,
    cable_candidates,
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
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
from netatlas.history.services import child_history, item_history
from netatlas.imports.services import (
    IMPORT_KINDS,
    MAX_ROWS,
    ImportFailed,
    import_fields,
    inspect_csv,
    preview_import,
    read_upload,
    run_import,
)
from netatlas.pages.attachments import (
    add_attachment,
    attachment_response,
    get_attachment,
    list_attachments,
)
from netatlas.pages.services import (
    UnconfirmedSectionRemoval,
    create_page_template,
    editor_sections,
    get_page_template,
    list_page_templates,
    update_page_template,
    create_page,
    editor_body,
    get_page,
    list_pages,
    live_field_options,
    mention_options,
    update_page,
)
from netatlas.places.services import (
    create_customer,
    create_site,
    get_customer,
    get_site,
    list_customers,
    list_sites,
    update_customer,
    update_site,
)
from netatlas.search.services import search
from netatlas.system.services import get_system_status
from netatlas.tagging.services import (
    add_tag,
    create_tag,
    filter_by_tags,
    list_tags,
    remove_tag,
    tag_usage,
    update_tag,
)
from netatlas.web.page_html import page_html


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


def _rack_height(request) -> int:
    try:
        return forms.IntegerField(min_value=1, max_value=100).clean(
            request.POST.get("height", "")
        )
    except ValidationError as error:
        raise ValidationError({"height": error.messages}) from error


def _render_racks(request, errors=(), form=None):
    context = {
        "racks": _with_tags(request, "rack", list_racks(request.user)),
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


@require_POST
def interface_vlans(request, interface_id):
    require_editor(request.user)
    try:
        untagged = _posted_id(request, "untagged_vlan_id")
        tagged = _posted_ids(request, "tagged_vlan_ids")
        set_interface_vlans(request.user, interface_id, untagged, tagged)
    except ValidationError as error:
        return _render_interface(request, interface_id, errors=error.messages)
    return redirect("web:interface", interface_id=interface_id)


def _posted_ids(request, field: str) -> list[int]:
    try:
        return [int(value) for value in request.POST.getlist(field)]
    except ValueError as error:
        raise ValidationError({field: [_("This item does not exist.")]}) from error


@require_http_methods(["GET", "POST"])
def vlans(request):
    if request.method == "GET":
        return _render_vlans(request)

    require_editor(request.user)
    try:
        create_vlan(
            request.user,
            number=_vlan_number(request),
            site_id=_required_id(request, "site_id"),
            name=request.POST.get("name", ""),
        )
    except ValidationError as error:
        return _render_vlans(request, errors=error.messages, form=request.POST)
    return redirect("web:vlans")


@require_http_methods(["GET", "POST"])
def vlan(request, vlan_id):
    if request.method == "GET":
        return _render_vlan(request, vlan_id)

    require_editor(request.user)
    try:
        update_vlan(
            request.user,
            vlan_id,
            number=_vlan_number(request),
            name=request.POST.get("name", ""),
        )
    except ValidationError as error:
        return _render_vlan(request, vlan_id, errors=error.messages, form=request.POST)
    return redirect("web:vlan", vlan_id=vlan_id)


def _vlan_number(request) -> int:
    try:
        return forms.IntegerField(min_value=1, max_value=4094).clean(
            request.POST.get("number", "")
        )
    except ValidationError as error:
        raise ValidationError({"number": error.messages}) from error


def _render_vlans(request, errors=(), form=None):
    context = {
        "vlans": _with_tags(request, "vlan", list_vlans(request.user)),
        "sites": list_sites(request.user),
        "selected_site": _submitted_id(request, "site_id"),
        "errors": errors,
        "form": form or {},
    }
    return render(request, "netatlas/vlans.html", context, status=400 if errors else 200)


def _render_vlan(request, vlan_id, errors=(), form=None):
    usage = vlan_usage(request.user, vlan_id)
    context = {
        "vlan": usage.vlan,
        "usage": usage,
        "sites": list_sites(request.user),
        "entries": item_history(request.user, "vlan", vlan_id),
        "errors": errors,
        "form": form or {"number": usage.vlan.number, "name": usage.vlan.name},
    }
    return render(request, "netatlas/vlan.html", context, status=400 if errors else 200)


@require_http_methods(["GET", "POST"])
def subnets(request):
    if request.method == "GET":
        return _render_subnets(request)

    require_editor(request.user)
    try:
        create_subnet(
            request.user,
            cidr=request.POST.get("cidr", ""),
            site_id=_required_id(request, "site_id"),
            vlan_id=_posted_id(request, "vlan_id"),
        )
    except ValidationError as error:
        return _render_subnets(request, errors=error.messages, form=request.POST)
    return redirect("web:subnets")


@require_http_methods(["GET", "POST"])
def subnet(request, subnet_id):
    if request.method == "GET":
        return _render_subnet(request, subnet_id)

    require_editor(request.user)
    try:
        # The field is absent while the Site has no VLANs, which must change nothing.
        vlan = {"vlan_id": _posted_id(request, "vlan_id")} if "vlan_id" in request.POST else {}
        update_subnet(
            request.user,
            subnet_id,
            cidr=request.POST.get("cidr", ""),
            site_id=_required_id(request, "site_id"),
            **vlan,
        )
    except ValidationError as error:
        return _render_subnet(request, subnet_id, errors=error.messages, form=request.POST)
    return redirect("web:subnet", subnet_id=subnet_id)


@require_POST
def subnet_ip_addresses(request, subnet_id):
    require_editor(request.user)
    address = request.POST.get("address", "")
    try:
        reserve_ip_address(
            request.user, subnet_id, address, description=request.POST.get("description", "")
        )
    except ValidationError as error:
        return _render_subnet(request, subnet_id, errors=error.messages, new_address=address)
    return redirect("web:subnet", subnet_id=subnet_id)


@require_POST
def subnet_dhcp_ranges(request, subnet_id):
    require_editor(request.user)
    try:
        create_dhcp_range(
            request.user,
            subnet_id,
            request.POST.get("start", ""),
            request.POST.get("end", ""),
            description=request.POST.get("description", ""),
        )
    except ValidationError as error:
        return _render_subnet(request, subnet_id, errors=error.messages, range_form=request.POST)
    return redirect("web:subnet", subnet_id=subnet_id)


@require_http_methods(["GET", "POST"])
def dhcp_range(request, range_id):
    if request.method == "GET":
        return _render_dhcp_range(request, range_id)

    require_editor(request.user)
    try:
        update_dhcp_range(
            request.user,
            range_id,
            start=request.POST.get("start", ""),
            end=request.POST.get("end", ""),
            description=request.POST.get("description", ""),
        )
    except ValidationError as error:
        return _render_dhcp_range(request, range_id, errors=error.messages, form=request.POST)
    return redirect("web:dhcp-range", range_id=range_id)


def _render_dhcp_range(request, range_id, errors=(), form=None):
    item = get_dhcp_range(request.user, range_id)
    context = {
        "dhcp_range": item,
        "entries": item_history(request.user, "dhcp-range", item.id),
        "errors": errors,
        "form": form
        or {"start": item.start, "end": item.end, "description": item.description},
    }
    return render(request, "netatlas/dhcp_range.html", context, status=400 if errors else 200)


@require_http_methods(["GET", "POST"])
def ip_addresses(request):
    if request.method == "GET":
        return _render_ip_addresses(request)

    require_editor(request.user)
    try:
        create_ip_address(
            request.user,
            address=request.POST.get("address", ""),
            site_id=_required_id(request, "site_id"),
            description=request.POST.get("description", ""),
        )
    except ValidationError as error:
        return _render_ip_addresses(request, errors=error.messages, form=request.POST)
    return redirect("web:ip-addresses")


@require_http_methods(["GET", "POST"])
def ip_address(request, address_id):
    if request.method == "GET":
        return _render_ip_address(request, address_id)

    require_editor(request.user)
    try:
        update_ip_address(
            request.user,
            address_id,
            address=request.POST.get("address", ""),
            description=request.POST.get("description", ""),
        )
    except ValidationError as error:
        return _render_ip_address(request, address_id, errors=error.messages, form=request.POST)
    return redirect("web:ip-address", address_id=address_id)


@require_POST
def ip_address_unassign(request, address_id):
    unassign_ip_address(request.user, address_id)
    return redirect("web:ip-address", address_id=address_id)


@require_POST
def interface_ip_addresses(request, interface_id):
    require_editor(request.user)
    try:
        assign_ip_address(request.user, interface_id, request.POST.get("address", ""))
    except ValidationError as error:
        return _render_interface(request, interface_id, errors=error.messages)
    return redirect("web:interface", interface_id=interface_id)


def _render_ip_addresses(request, errors=(), form=None):
    context = {
        "ip_addresses": _with_tags(request, "ip-address", list_ip_addresses(request.user)),
        "sites": list_sites(request.user),
        "selected_site": _submitted_id(request, "site_id"),
        "errors": errors,
        "form": form or {},
    }
    return render(request, "netatlas/ip_addresses.html", context, status=400 if errors else 200)


def _render_ip_address(request, address_id, errors=(), form=None):
    item = get_ip_address(request.user, address_id)
    context = {
        "ip_address": item,
        "sites": list_sites(request.user),
        "entries": item_history(request.user, "ip-address", item.id),
        "errors": errors,
        "form": form or {"address": item.address, "description": item.description},
    }
    return render(request, "netatlas/ip_address.html", context, status=400 if errors else 200)


def _render_subnets(request, errors=(), form=None):
    context = {
        "subnets": _with_tags(request, "subnet", list_subnets(request.user)),
        "sites": list_sites(request.user),
        "vlans": list_vlans(request.user),
        "selected_vlan": _submitted_id(request, "vlan_id"),
        "selected_site": _submitted_id(request, "site_id"),
        "errors": errors,
        "form": form or {},
    }
    return render(request, "netatlas/subnets.html", context, status=400 if errors else 200)


def _render_subnet(
    request, subnet_id, errors=(), form=None, new_address=None, range_form=None
):
    item = get_subnet(request.user, subnet_id)
    in_site = list_subnets(request.user, site_id=item.site_id)
    usage = subnet_usage(request.user, subnet_id)
    context = {
        "subnet": item,
        "usage": usage,
        "new_address": usage.next_free or "" if new_address is None else new_address,
        "range_form": range_form or {},
        "children": [entry for entry in in_site if entry.parent_id == item.id],
        "sites": list_sites(request.user),
        "vlans": list_vlans(request.user, site_id=item.site_id),
        "selected_vlan": _submitted_id(request, "vlan_id", item.vlan_id),
        "selected_site": _submitted_id(request, "site_id", item.site_id),
        "entries": item_history(request.user, "subnet", item.id),
        "errors": errors,
        "form": form or {"cidr": item.cidr},
    }
    return render(request, "netatlas/subnet.html", context, status=400 if errors else 200)


def cables(request):
    shown = _with_tags(request, "cable", list_cables(request.user))
    return render(request, "netatlas/cables.html", {"cables": shown})


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


def _submitted_tagged(request, item) -> set[int]:
    if "untagged_vlan_id" in request.POST or "tagged_vlan_ids" in request.POST:
        return {int(v) for v in request.POST.getlist("tagged_vlan_ids") if v.isdecimal()}
    return {vlan.id for vlan in item.tagged_vlans}


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
        # VLANs live on the LAG, so its members get no VLAN form.
        "vlan_choices": list_vlans(request.user, site_id=owner.site_id) if not item.lag_id else [],
        "selected_untagged": _submitted_id(
            request, "untagged_vlan_id", item.untagged_vlan.id if item.untagged_vlan else None
        ),
        "selected_tagged": _submitted_tagged(request, item),
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
        "devices": _with_tags(request, "device", all_devices),
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
        "device_types": _with_tags(request, "device-type", list_device_types(request.user)),
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
    customers_shown = _with_tags(request, "customer", list_customers(request.user))
    context = {"customers": customers_shown, "errors": errors, "name": name}
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
        "sites": _with_tags(request, "site", list_sites(request.user)),
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


@require_http_methods(["GET", "POST"])
def delete_confirmation(request, kind, item_id):
    """Show everything a delete affects, and delete only what the person saw."""
    require_editor(request.user)
    if request.method == "GET":
        return _render_delete(request, kind, item_id)

    preview = preview_delete(request.user, kind, item_id)
    if preview.blocked:
        return _render_delete(request, kind, item_id, status=400)
    target = _after_delete(request, kind, item_id)
    try:
        confirm_delete(request.user, kind, item_id, request.POST.get("fingerprint", ""))
    except ValidationError as error:
        return _render_delete(request, kind, item_id, errors=error.messages, status=400)
    except StaleDelete:
        message = _("Check what this delete affects, then confirm again.")
        return _render_delete(request, kind, item_id, errors=[message], status=409)
    return redirect(target)


def _after_delete(request, kind, item_id) -> str:
    """Where to go once the item is gone."""
    if kind == "interface":
        device_id = get_interface(request.user, item_id).device_id
        return reverse("web:device", args=[device_id])
    if kind == "dhcp-range":
        subnet_id = get_dhcp_range(request.user, item_id).subnet_id
        return reverse("web:subnet", args=[subnet_id])
    if kind == "attachment":
        return reverse("web:page", args=[get_attachment(request.user, item_id).page_id])
    lists = {"customer": "sites", "device-type": "device-types", "ip-address": "ip-addresses"}
    return reverse(f"web:{lists.get(kind, f'{kind}s')}")


def _kind_names() -> dict[str, str]:
    return {
        "customer": _("Customer"),
        "site": _("Site"),
        "device": _("Device"),
        "interface": _("Interface"),
        "rack": _("Rack"),
        "vlan": _("VLAN"),
        "subnet": _("Subnet"),
        "dhcp-range": _("DHCP Range"),
        "ip-address": _("IP Address"),
        "cable": _("Cable"),
        "device-type": _("Device Type"),
        "tag": _("Tag"),
        "page": _("Page"),
        "page-template": _("Page Template"),
        "attachment": _("Attachment"),
    }


def _impact_rows(impacts) -> list[dict]:
    names = _kind_names()
    return [
        {"kind": names[impact.kind], "label": impact.label, "effect": impact.effect}
        for impact in impacts
    ]


def _render_delete(request, kind, item_id, errors=(), status=200):
    preview = preview_delete(request.user, kind, item_id)
    context = {
        "preview": preview,
        "deleted": _impact_rows(preview.deleted),
        "changed": _impact_rows(preview.changed),
        "moved": _impact_rows(preview.moved),
        "broken_live_fields": _impact_rows(preview.broken_live_fields),
        "broken_embeds": _impact_rows(preview.broken_embeds),
        "item_url": (
            reverse("web:page", args=[get_attachment(request.user, item_id).page_id])
            if kind == "attachment"
            else reverse(f"web:{kind}", args=[item_id])
        ),
        "errors": [*errors, *preview.blocked],
    }
    return render(request, "netatlas/delete_confirmation.html", context, status=status)


def _with_tags(request, kind, items):
    """The items carrying every Tag chosen in the list's filter."""
    names = [name for name in request.GET.getlist("tag") if name.strip()]
    return filter_by_tags(kind, items, names) if names else items


@require_http_methods(["GET", "POST"])
def pages(request):
    if request.method == "GET":
        return _render_pages(request)

    require_editor(request.user)
    title = request.POST.get("title", "")
    body = request.POST.get("body", "")
    try:
        created = create_page(request.user, title, body)
    except ValidationError as error:
        return _render_pages(request, errors=error.messages, title=title, body=body)
    return redirect("web:page", page_id=created.id)


def page(request, page_id):
    return _render_page(request, page_id)


@require_POST
def page_attachments(request, page_id):
    require_editor(request.user)
    try:
        add_attachment(request.user, page_id, request.FILES.get("file"))
    except ValidationError as error:
        return _render_page(request, page_id, errors=error.messages, status=400)
    return redirect("web:page", page_id=page_id)


def attachment(request, attachment_id):
    return attachment_response(request.user, attachment_id)


def _render_page(request, page_id, errors=(), status=200):
    item = get_page(request.user, page_id)
    item_url = reverse(f"web:{item.item_kind}", args=[item.item_id]) if item.item_id else None
    entries = [
        *item_history(request.user, "page", page_id),
        *child_history(request.user, "attachment", "page", page_id),
    ]
    context = {
        "page": item,
        "html": page_html(request.user, page_id),
        "item_url": item_url,
        "attachments": list_attachments(request.user, page_id),
        "entries": sorted(entries, key=lambda entry: (entry.at, entry.id), reverse=True),
        "errors": errors,
    }
    return render(request, "netatlas/page.html", context, status=status)


@require_http_methods(["GET", "POST"])
def page_edit(request, page_id):
    require_editor(request.user)
    if request.method == "GET":
        return _render_page_edit(request, page_id)

    item = get_page(request.user, page_id)
    title = request.POST.get("title", "") if item.item_id is None else None
    body = request.POST.get("body", "")
    sections = None
    if item.item_id is not None and "section_name" in request.POST:
        names = request.POST.getlist("section_name")
        sections = dict(zip(names, request.POST.getlist("section_content"), strict=False))
        body = None
    try:
        update_page(request.user, page_id, title=title, body=body, sections=sections)
    except ValidationError as error:
        return _render_page_edit(
            request, page_id, errors=error.messages, title=title, body=body, sections=sections
        )
    return redirect("web:page", page_id=page_id)


def page_mention_options(request):
    names = _kind_names()
    options = [
        {"token": option.token, "label": option.label, "kind_name": names[option.kind]}
        for option in mention_options(request.user, request.GET.get("q", ""))
    ]
    return render(request, "netatlas/_mention_options.html", {"options": options})


def page_live_field_options(request):
    names = _kind_names()
    options = [
        {"token": option.token, "label": option.label, "kind_name": names[option.kind]}
        for option in live_field_options(request.user, request.GET.get("q", ""))
    ]
    return render(request, "netatlas/_mention_options.html", {"options": options})


def _render_pages(request, errors=(), title="", body=""):
    context = {
        "pages": _with_tags(request, "page", list_pages(request.user)),
        "errors": errors,
        "title": title,
        "body": body,
    }
    return render(request, "netatlas/pages.html", context, status=400 if errors else 200)


def _render_page_edit(request, page_id, errors=(), title=None, body=None, sections=None):
    item = get_page(request.user, page_id)
    shown_sections = [
        {
            "name": section.name,
            "content": sections.get(section.name, section.content) if sections else section.content,
            "in_template": section.in_template,
        }
        for section in editor_sections(request.user, page_id)
    ]
    context = {
        "page": item,
        "sections": shown_sections,
        "show_title": item.item_id is None,
        "title": item.title if title is None else title,
        "body": editor_body(request.user, page_id) if body is None else body,
        "errors": errors,
    }
    return render(request, "netatlas/page_edit.html", context, status=400 if errors else 200)


_OIDC_NEXT = "oidc_next"


@login_not_required
@require_POST
def oidc_login(request):
    """Starts on POST, so another site cannot send someone through a login."""
    if not settings.OIDC:
        raise Http404
    wanted = request.POST.get("next", "")
    safe = url_has_allowed_host_and_scheme(
        wanted, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    request.session[_OIDC_NEXT] = wanted if safe else ""
    try:
        return begin_login(request, request.build_absolute_uri(reverse("web:oidc-callback")))
    except OidcLoginFailed as error:
        messages.error(request, str(error))
        return redirect("web:login")


@login_not_required
def oidc_callback(request):
    if not settings.OIDC:
        raise Http404
    target = request.session.pop(_OIDC_NEXT, "")
    try:
        user = finish_login(request)
    except OidcLoginFailed as error:
        messages.error(request, str(error))
        return redirect("web:login")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(target or settings.LOGIN_REDIRECT_URL)


MAX_PREVIEW_ROWS = 50


@require_http_methods(["GET", "POST"])
def imports(request):
    """Upload a CSV file, match its columns, preview it, then import it."""
    require_editor(request.user)
    if request.method == "GET":
        return _render_import_upload(request)

    step = request.POST.get("step")
    kind = request.POST.get("kind", "")
    if step == "upload":
        try:
            text = read_upload(request.FILES.get("file"))
            columns = inspect_csv(request.user, kind, text)
        except ValidationError as error:
            return _render_import_upload(request, kind, error.messages)
        return _render_import_map(request, kind, text, columns, columns.mapping)

    text = request.POST.get("csv", "")
    try:
        columns = inspect_csv(request.user, kind, text)
    except ValidationError as error:
        return _render_import_upload(request, kind, error.messages)
    prefix = "map_"
    mapping = {
        key.removeprefix(prefix): value
        for key, value in request.POST.items()
        if key.startswith(prefix) and value
    }
    try:
        if step == "preview":
            result = preview_import(request.user, kind, text, mapping)
            return _render_import_preview(request, kind, text, mapping, result)
        if step == "import":
            result = run_import(request.user, kind, text, mapping)
            names = _kind_names()
            messages.success(
                request,
                ngettext(
                    "Imported %(count)d row: %(kind)s.",
                    "Imported %(count)d rows: %(kind)s.",
                    result.total,
                )
                % {"count": result.total, "kind": names[kind]},
            )
            return redirect("web:imports")
    except ImportFailed as failed:
        return _render_import_preview(request, kind, text, mapping, failed.preview, status=409)
    except ValidationError as error:
        return _render_import_map(request, kind, text, columns, mapping, error.messages)
    return _render_import_upload(request, kind, [_("Upload a CSV file to start.")])


def _render_import_upload(request, kind="", errors=()):
    names = _kind_names()
    context = {
        "step": "upload",
        "kind": kind,
        "kinds": [{"value": value, "label": names[value]} for value in IMPORT_KINDS],
        "max_rows": MAX_ROWS,
        "errors": errors,
    }
    return render(request, "netatlas/imports.html", context, status=400 if errors else 200)


def _render_import_map(request, kind, text, columns, mapping, errors=()):
    context = {
        "step": "map",
        "kind": kind,
        "kind_name": _kind_names()[kind],
        "csv": text,
        "columns": columns,
        "fields": [
            {
                "name": field.name,
                "label": field.label,
                "required": field.required,
                "selected": mapping.get(field.name, ""),
            }
            for field in import_fields(kind)
        ],
        "errors": errors,
    }
    return render(request, "netatlas/imports.html", context, status=400 if errors else 200)


def _render_import_preview(request, kind, text, mapping, result, status=200):
    fields = [field for field in import_fields(kind) if field.name in mapping]
    invalid = [row for row in result.rows if row.errors]
    valid = [row for row in result.rows if not row.errors]
    # Every error is shown, because each one has to be fixed.
    room = max(MAX_PREVIEW_ROWS - len(invalid), 0)
    chosen = sorted([*invalid, *valid[:room]], key=lambda row: row.number)
    context = {
        "step": "preview",
        "kind": kind,
        "kind_name": _kind_names()[kind],
        "csv": text,
        "mapping": mapping,
        "preview": result,
        "invalid": len(invalid),
        "fields": [{"name": field.name, "label": field.label} for field in fields],
        "shown_rows": [
            {
                "number": row.number,
                "cells": [row.values[field.name] for field in fields],
                "errors": row.errors,
            }
            for row in chosen
        ],
        "hidden_rows": max(len(valid) - room, 0),
    }
    return render(request, "netatlas/imports.html", context, status=status)


def search_page(request):
    query = request.GET.get("q", "")
    chosen_tags = [name for name in request.GET.getlist("tag") if name.strip()]
    site = request.GET.get("site", "")
    searched = bool(query.strip() or chosen_tags or site)
    errors = []
    results = None
    site_id = None
    try:
        if site:
            if not (site.isascii() and site.isdecimal() and len(site) <= 18):
                raise ValidationError(_("Choose an existing Site."))
            site_id = int(site)
        results = search(request.user, query, chosen_tags, site_id)
    except ValidationError as error:
        errors = error.messages

    names = _kind_names()
    groups = [
        {
            "name": names[group.kind],
            "count": group.count,
            "more": group.count > len(group.hits),
            "hits": [
                {
                    "label": hit.label,
                    "detail": hit.detail,
                    "excerpt": hit.excerpt,
                    "url": reverse(f"web:{hit.kind}", args=[hit.id]),
                }
                for hit in group.hits
            ],
        }
        for group in (results.groups if results else [])
    ]
    keys = {name.strip().casefold() for name in chosen_tags}
    context = {
        "query": query,
        "searched": searched,
        "results": results,
        "groups": groups,
        "errors": errors,
        "sites": list_sites(request.user),
        "selected_site": site_id,
        "tags": [
            {"name": tag.name, "selected": tag.name.casefold() in keys}
            for tag in list_tags(request.user)
        ],
    }
    return render(request, "netatlas/search.html", context, status=400 if errors else 200)


@require_http_methods(["GET", "POST"])
def page_templates(request):
    if request.method == "GET":
        return _render_page_templates(request)

    require_editor(request.user)
    name = request.POST.get("name", "")
    body = request.POST.get("body", "")
    device_type = request.POST.get("device_type_id", "")
    try:
        if not device_type.isascii() or not device_type.isdecimal():
            raise ValidationError(_("Choose an existing Device Type."))
        created = create_page_template(request.user, name, int(device_type), body)
    except (ValidationError, OverflowError) as error:
        messages = (
            error.messages
            if isinstance(error, ValidationError)
            else [_("Choose an existing Device Type.")]
        )
        return _render_page_templates(
            request, errors=messages, name=name, body=body, device_type=device_type
        )
    return redirect("web:page-template", template_id=created.id)


@require_http_methods(["GET", "POST"])
def page_template(request, template_id):
    if request.method == "GET":
        return _render_page_template(request, template_id)

    require_editor(request.user)
    name = request.POST.get("name", "")
    body = request.POST.get("body", "")
    try:
        confirmed = json.loads(request.POST.get("confirmed_removals") or "{}")
    except ValueError:
        confirmed = {}
    try:
        update_page_template(
            request.user, template_id, name=name, body=body, confirmed_removals=confirmed
        )
    except UnconfirmedSectionRemoval as needed:
        return _render_page_template(
            request, template_id, name=name, body=body, removed=needed.removed, status=409
        )
    except ValidationError as error:
        return _render_page_template(
            request, template_id, errors=error.messages, name=name, body=body, status=400
        )
    return redirect("web:page-template", template_id=template_id)


def _render_page_templates(request, errors=(), name="", body=None, device_type=""):
    templates = list_page_templates(request.user)
    taken = {template.device_type_id for template in templates}
    context = {
        "templates": templates,
        "device_types": [d for d in list_device_types(request.user) if d.id not in taken],
        "selected_device_type": int(device_type) if device_type.isdecimal() else None,
        "errors": errors,
        "name": name,
        "body": "{{section:Notes}}" if body is None else body,
    }
    return render(request, "netatlas/page_templates.html", context, status=400 if errors else 200)


def _render_page_template(
    request, template_id, errors=(), name=None, body=None, removed=(), status=200
):
    template = get_page_template(request.user, template_id)
    context = {
        "template": template,
        "name": template.name if name is None else name,
        "body": template.body if body is None else body,
        "removed": removed,
        "confirmed_removals": json.dumps({entry.name: entry.page_count for entry in removed}),
        "entries": item_history(request.user, "page-template", template_id),
        "errors": errors,
    }
    return render(request, "netatlas/page_template.html", context, status=status)


@require_http_methods(["GET", "POST"])
def tags(request):
    if request.method == "GET":
        return _render_tags(request)

    require_editor(request.user)
    name = request.POST.get("name", "")
    try:
        create_tag(request.user, name)
    except ValidationError as error:
        return _render_tags(request, errors=error.messages, name=name)
    return redirect("web:tags")


@require_http_methods(["GET", "POST"])
def tag(request, tag_id):
    if request.method == "GET":
        return _render_tag(request, tag_id)

    require_editor(request.user)
    name = request.POST.get("name", "")
    try:
        update_tag(request.user, tag_id, name)
    except ValidationError as error:
        return _render_tag(request, tag_id, errors=error.messages, name=name)
    return redirect("web:tag", tag_id=tag_id)


@require_POST
def item_tags(request, kind, item_id):
    require_editor(request.user)
    try:
        add_tag(request.user, kind, item_id, request.POST.get("name", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    return redirect(f"web:{kind}", item_id)


@require_POST
def item_tag_remove(request, kind, item_id, tag_id):
    remove_tag(request.user, kind, item_id, tag_id)
    return redirect(f"web:{kind}", item_id)


def _render_tags(request, errors=(), name=""):
    context = {"tags": list_tags(request.user), "errors": errors, "name": name}
    return render(request, "netatlas/tags.html", context, status=400 if errors else 200)


def _render_tag(request, tag_id, errors=(), name=None):
    usage = tag_usage(request.user, tag_id)
    context = {
        "tag": usage.tag,
        "items": [
            {
                "kind": item.kind,
                "label": item.label,
                "url": reverse(f"web:{item.kind}", args=[item.id]),
            }
            for item in usage.items
        ],
        "entries": item_history(request.user, "tag", tag_id),
        "errors": errors,
        "name": usage.tag.name if name is None else name,
    }
    return render(request, "netatlas/tag.html", context, status=400 if errors else 200)
