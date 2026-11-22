from django.urls import path
from drf_spectacular.views import SpectacularJSONAPIView

from netatlas.api import views

app_name = "api"

urlpatterns = [
    path("schema/", SpectacularJSONAPIView.as_view(), name="schema"),
    path("status/", views.StatusView.as_view(), name="status"),
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("users/<int:user_id>/", views.UserDetailView.as_view(), name="user-detail"),
    path("tokens/", views.TokenListView.as_view(), name="token-list"),
    path("tokens/<int:token_id>/", views.TokenDetailView.as_view(), name="token-detail"),
]
