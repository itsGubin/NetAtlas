from netatlas.accounts.roles import Role
from netatlas.accounts.services import role_of


def navigation(request):
    """Evaluated by the template only when needed, so public pages touch no database."""
    return {"is_admin": lambda: role_of(request.user) == Role.ADMIN}
