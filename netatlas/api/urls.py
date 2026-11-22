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
    path("history/<slug:kind>/<int:item_id>/", views.HistoryView.as_view(), name="history"),
    path("customers/", views.CustomerListView.as_view(), name="customer-list"),
    path(
        "customers/<int:customer_id>/", views.CustomerDetailView.as_view(), name="customer-detail"
    ),
    path("sites/", views.SiteListView.as_view(), name="site-list"),
    path("sites/<int:site_id>/", views.SiteDetailView.as_view(), name="site-detail"),
    path("device-types/", views.DeviceTypeListView.as_view(), name="device-type-list"),
    path(
        "device-types/<int:device_type_id>/",
        views.DeviceTypeDetailView.as_view(),
        name="device-type-detail",
    ),
]
