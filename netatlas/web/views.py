from django.shortcuts import render

from netatlas.system.services import get_system_status


def home(request):
    return render(request, "netatlas/home.html")


def status(request):
    return render(request, "netatlas/status.html", {"status": get_system_status()})
