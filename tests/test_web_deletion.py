import re

import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    create_vlan,
    get_ip_address,
)
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
    get_device,
    list_devices,
)
from netatlas.places.services import create_customer, create_site
from tests.helpers import confirm_delete


def root():
    return authenticate(username="root", password="correct-horse-battery")


def items():
    """One item of every kind, as (URL prefix, id, where a delete returns to)."""
    site = create_site(root(), name="Home")
    create_site(root(), name="Office")
    customer = create_customer(root(), name="Acme")
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    first = create_interface(root(), device.id, name="eth0", kind="physical")
    second = create_interface(root(), device.id, name="eth1", kind="physical")
    spare = create_interface(root(), device.id, name="eth2", kind="physical")
    cable = create_cable(root(), first.id, second.id)
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    vlan = create_vlan(root(), number=10, site_id=site.id)
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    empty = create_subnet(root(), cidr="10.0.9.0/24", site_id=site.id)
    address = create_ip_address(root(), address="10.0.0.5", site_id=site.id)
    dhcp = create_dhcp_range(root(), subnet.id, "10.0.0.100", "10.0.0.110")
    template = create_device_type(root(), make="Acme", model_name="SW")
    other_site = create_site(root(), name="Spare")
    return {
        "customer": (f"/customers/{customer.id}/", "/sites/"),
        "site": (f"/sites/{other_site.id}/", "/sites/"),
        "interface": (f"/interfaces/{spare.id}/", f"/devices/{device.id}/"),
        "cable": (f"/cables/{cable.id}/", "/cables/"),
        "rack": (f"/racks/{rack.id}/", "/racks/"),
        "vlan": (f"/vlans/{vlan.id}/", "/vlans/"),
        "subnet": (f"/subnets/{empty.id}/", "/subnets/"),
        "ip-address": (f"/ip-addresses/{address.id}/", "/ip-addresses/"),
        "dhcp-range": (f"/dhcp-ranges/{dhcp.id}/", f"/subnets/{subnet.id}/"),
        "device-type": (f"/device-types/{template.id}/", "/device-types/"),
        "device": (f"/devices/{device.id}/", "/devices/"),
    }


KINDS = [
    "customer",
    "site",
    "interface",
    "cable",
    "rack",
    "vlan",
    "subnet",
    "ip-address",
    "dhcp-range",
    "device-type",
    "device",
]


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_deleted_only_after_confirmation(client_with_role, kind):
    client = client_with_role(Role.EDITOR)
    item_path, back = items()[kind]
    delete_path = f"{item_path}delete/"

    item_page = client.get(item_path).content.decode()
    assert f'<a href="{delete_path}">' in item_page
    assert f'action="{delete_path}"' not in item_page

    page = client.get(delete_path)
    assert page.status_code == 200
    assert f'<a href="{item_path}">' in page.content.decode()
    assert client.get(item_path).status_code == 200

    response = confirm_delete(client, delete_path)
    assert response.status_code == 302
    assert response["Location"] == back
    assert client.get(item_path).status_code == 404


@pytest.mark.parametrize("kind", KINDS)
def test_post_without_confirmation_deletes_nothing(client_with_role, kind):
    client = client_with_role(Role.EDITOR)
    item_path, _back = items()[kind]

    response = client.post(f"{item_path}delete/")

    assert response.status_code == 409
    assert client.get(item_path).status_code == 200


def test_page_lists_everything_affected(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    other = create_device(root(), name="switch", kind="physical", site_id=site.id)
    port = create_interface(root(), device.id, name="eth0", kind="physical")
    far = create_interface(root(), other.id, name="p1", kind="physical")
    create_cable(root(), port.id, far.id, label="A-1")
    assign_ip_address(root(), port.id, "10.0.0.5")

    page = client.get(f"/devices/{device.id}/delete/").content.decode()

    assert "server eth0" in page
    assert "A-1: server eth0" in page
    assert "10.0.0.5" in page
    assert "becomes Reserved" in page


def test_page_lists_what_moves_up(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_subnet(root(), cidr="10.0.0.0/16", site_id=site.id)
    middle = create_subnet(root(), cidr="10.0.1.0/24", site_id=site.id)
    create_subnet(root(), cidr="10.0.1.0/25", site_id=site.id)

    page = client.get(f"/subnets/{middle.id}/delete/").content.decode()

    assert "10.0.1.0/25" in page
    assert "moves to 10.0.0.0/16" in page


def test_changed_impact_asks_again_with_the_new_list(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    path = f"/devices/{device.id}/delete/"
    old = re.search(r'name="fingerprint" value="(\w+)"', client.get(path).content.decode())[1]
    create_interface(root(), device.id, name="added-later", kind="physical")

    response = client.post(path, {"fingerprint": old})

    page = response.content.decode()
    assert response.status_code == 409
    assert 'role="alert"' in page
    assert "added-later" in page
    assert f'name="fingerprint" value="{old}"' not in page
    assert get_device(root(), device.id).name == "server"


def test_blocked_delete_shows_why_and_offers_no_button(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    create_device(root(), name="vm-one", kind="virtual", parent_id=host.id)
    path = f"/devices/{host.id}/delete/"

    page = client.get(path).content.decode()

    assert "vm-one" in page
    assert 'name="fingerprint"' not in page
    response = client.post(path, {"fingerprint": "anything"})
    assert response.status_code == 400
    assert "Move or delete its Virtual Devices first" in response.content.decode()
    assert len(list_devices(root())) == 2


@pytest.mark.parametrize("fingerprint", ["nonsense", "überall", "a\x00b"])
def test_bad_confirmation_values_delete_nothing(client_with_role, fingerprint):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="server", kind="physical", site_id=site.id)

    response = client.post(f"/devices/{device.id}/delete/", {"fingerprint": fingerprint})

    assert response.status_code == 409
    assert get_device(root(), device.id).name == "server"


def test_viewer_gets_no_link_and_no_confirmation_page(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    path = f"/devices/{device.id}/delete/"

    assert path not in client.get(f"/devices/{device.id}/").content.decode()
    assert client.get(path).status_code == 403
    assert client.post(path, {"fingerprint": "x"}).status_code == 403


@pytest.mark.django_db
def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/devices/1/delete/")

    assert response.status_code == 302
    assert response["Location"] == "/login/?next=/devices/1/delete/"


def test_unknown_item_is_not_found(client_with_role):
    client = client_with_role(Role.EDITOR)

    assert client.get("/devices/999999/delete/").status_code == 404
    assert client.post("/devices/999999/delete/", {"fingerprint": "x"}).status_code == 404


def test_confirmed_device_delete_keeps_addresses_reserved(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    port = create_interface(root(), device.id, name="eth0", kind="physical")
    address = assign_ip_address(root(), port.id, "10.0.0.5")

    confirm_delete(client, f"/devices/{device.id}/delete/")

    assert get_ip_address(root(), address.id).status == "reserved"
