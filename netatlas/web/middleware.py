from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.shortcuts import render

from netatlas import __version__
from netatlas.system.services import is_database_outage


class WebLoginRequiredMiddleware(LoginRequiredMiddleware):
    """Every web page requires login unless it opts out. The API answers with its own errors.

    Loading a session needs the database, so an outage is answered here with a 503 page.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        if _is_api(request):
            return None
        try:
            return super().process_view(request, view_func, view_args, view_kwargs)
        except Exception as error:
            if is_database_outage(error):
                return _unavailable(request)
            raise

    def process_exception(self, request, exception):
        if not _is_api(request) and is_database_outage(exception):
            return _unavailable(request)
        return None


def _is_api(request):
    return request.path_info.startswith("/api/")


def _unavailable(request):
    return render(request, "netatlas/unavailable.html", {"version": __version__}, status=503)
