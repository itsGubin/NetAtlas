from django.urls import path

from netatlas.web import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("status/", views.status, name="status"),
]
