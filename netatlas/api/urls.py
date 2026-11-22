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
    path(
        "delete-preview/<slug:kind>/<int:item_id>/",
        views.DeletePreviewView.as_view(),
        name="delete-preview",
    ),
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
    path(
        "interfaces/<int:interface_id>/vlans/",
        views.InterfaceVlansView.as_view(),
        name="interface-vlans",
    ),
    path("vlans/", views.VlanListView.as_view(), name="vlan-list"),
    path("vlans/<int:vlan_id>/", views.VlanDetailView.as_view(), name="vlan-detail"),
    path("vlans/<int:vlan_id>/usage/", views.VlanUsageView.as_view(), name="vlan-usage"),
    path("ip-addresses/", views.IpAddressListView.as_view(), name="ip-address-list"),
    path(
        "ip-addresses/<int:address_id>/",
        views.IpAddressDetailView.as_view(),
        name="ip-address-detail",
    ),
    path(
        "interfaces/<int:interface_id>/ip-addresses/",
        views.InterfaceIpAddressesView.as_view(),
        name="interface-ip-addresses",
    ),
    path("dhcp-ranges/", views.DhcpRangeListView.as_view(), name="dhcp-range-list"),
    path(
        "dhcp-ranges/<int:range_id>/",
        views.DhcpRangeDetailView.as_view(),
        name="dhcp-range-detail",
    ),
    path("subnets/<int:subnet_id>/usage/", views.SubnetUsageView.as_view(), name="subnet-usage"),
    path("subnets/", views.SubnetListView.as_view(), name="subnet-list"),
    path("subnets/<int:subnet_id>/", views.SubnetDetailView.as_view(), name="subnet-detail"),
    path("pages/", views.PageListView.as_view(), name="page-list"),
    path("pages/<int:page_id>/", views.PageDetailView.as_view(), name="page-detail"),
    path(
        "pages/<int:page_id>/live-fields/",
        views.PageLiveFieldsView.as_view(),
        name="page-live-fields",
    ),
    path("live-field-options/", views.LiveFieldOptionsView.as_view(), name="live-field-options"),
    path("mention-options/", views.MentionOptionsView.as_view(), name="mention-options"),
    path("items/<slug:kind>/<int:item_id>/page/", views.ItemPageView.as_view(), name="item-page"),
    path(
        "items/<slug:kind>/<int:item_id>/referenced-by/",
        views.ItemReferencesView.as_view(),
        name="item-references",
    ),
    path("tags/", views.TagListView.as_view(), name="tag-list"),
    path("tags/<int:tag_id>/", views.TagDetailView.as_view(), name="tag-detail"),
    path(
        "items/<slug:kind>/<int:item_id>/tags/",
        views.ItemTagsView.as_view(),
        name="item-tags",
    ),
    path(
        "items/<slug:kind>/<int:item_id>/tags/<int:tag_id>/",
        views.ItemTagDetailView.as_view(),
        name="item-tag-detail",
    ),
    path("cables/", views.CableListView.as_view(), name="cable-list"),
    path("cables/<int:cable_id>/", views.CableDetailView.as_view(), name="cable-detail"),
]
