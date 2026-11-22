from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from netatlas.accounts.roles import Role
from netatlas.accounts.services import (
    assign_role,
    create_user,
    get_user,
    list_users,
    require_admin,
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


def _render_users(request, errors=(), username="", new_role=Role.VIEWER):
    context = {
        "users": list_users(request.user),
        "roles": Role.choices,
        "errors": errors,
        "username": username,
        "new_role": new_role,
    }
    return render(request, "netatlas/users.html", context, status=400 if errors else 200)
