from django.urls import include, path

urlpatterns = [
    path("api/", include("netatlas.api.urls")),
    path("", include("netatlas.web.urls")),
]
