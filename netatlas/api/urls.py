from django.urls import path

from netatlas.api import views

app_name = "api"

urlpatterns = [
    path("status/", views.StatusView.as_view(), name="status"),
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("users/<int:user_id>/", views.UserDetailView.as_view(), name="user-detail"),
]
