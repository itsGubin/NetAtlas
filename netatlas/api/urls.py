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
    path("devices/", views.DeviceListView.as_view(), name="device-list"),
    path("devices/<int:device_id>/", views.DeviceDetailView.as_view(), name="device-detail"),
    path(
        "devices/<int:device_id>/virtual-devices/",
        views.VirtualDeviceListView.as_view(),
        name="device-virtual-devices",
    ),
    path(
        "devices/<int:device_id>/interfaces/",
        views.InterfaceListView.as_view(),
        name="device-interfaces",
    ),
    path(
        "interfaces/<int:interface_id>/",
        views.InterfaceDetailView.as_view(),
        name="interface-detail",
    ),
    path(
        "interfaces/<int:interface_id>/trace/",
        views.InterfaceTraceView.as_view(),
        name="interface-trace",
    ),
    path(
        "interfaces/<int:interface_id>/pair/",
        views.InterfacePairView.as_view(),
        name="interface-pair",
    ),
    path(
        "devices/<int:device_id>/placement/",
        views.DevicePlacementView.as_view(),
        name="device-placement",
    ),
    path("racks/", views.RackListView.as_view(), name="rack-list"),
    path("racks/<int:rack_id>/", views.RackDetailView.as_view(), name="rack-detail"),
    path(
        "racks/<int:rack_id>/elevation/",
        views.RackElevationView.as_view(),
        name="rack-elevation",
    ),
    path("subnets/", views.SubnetListView.as_view(), name="subnet-list"),
    path("subnets/<int:subnet_id>/", views.SubnetDetailView.as_view(), name="subnet-detail"),
    path("cables/", views.CableListView.as_view(), name="cable-list"),
    path("cables/<int:cable_id>/", views.CableDetailView.as_view(), name="cable-detail"),
]
