from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.cache import patch_cache_control
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
    revoke_token,
    role_of,
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
