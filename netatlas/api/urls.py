from django.urls import path

from netatlas.api import views

app_name = "api"

urlpatterns = [
    path("status/", views.StatusView.as_view(), name="status"),
]
