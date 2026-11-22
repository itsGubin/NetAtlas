from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from netatlas.web import views

app_name = "web"

urlpatterns = [
    path("login/", LoginView.as_view(template_name="netatlas/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", views.home, name="home"),
    path("status/", views.status, name="status"),
    path("users/", views.users, name="users"),
    path("users/<int:user_id>/role/", views.user_role, name="user-role"),
    path("history/<slug:kind>/<int:item_id>/", views.history, name="history"),
    path("tokens/", views.tokens, name="tokens"),
    path("tokens/<int:token_id>/revoke/", views.token_revoke, name="token-revoke"),
]
