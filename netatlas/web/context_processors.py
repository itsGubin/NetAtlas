from netatlas.accounts.oidc import provider_name
from netatlas.accounts.roles import Role
from netatlas.accounts.services import role_of
from netatlas.places.services import has_customers


def navigation(request):
    """Evaluated by the template only when needed, so public pages touch no database."""
    return {
        "is_admin": lambda: role_of(request.user) == Role.ADMIN,
        "can_edit": lambda: role_of(request.user) in (Role.ADMIN, Role.EDITOR),
        "has_customers": has_customers,
        "oidc_provider": provider_name,
    }
