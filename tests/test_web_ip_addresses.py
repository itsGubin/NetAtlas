import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    get_dhcp_range,
    get_ip_address,
    list_dhcp_ranges,
    list_ip_addresses,
)
from netatlas.devices.services import create_device, create_interface
from netatlas.places.services import create_site

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def plan():
    site = create_site(root(), name="Home")
    subnet = create_subnet(root(), cidr="192.168.1.0/24", site_id=site.id)
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    port = create_interface(root(), device.id, name="eth0", kind="physical")
    return site, subnet, port


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/ip-addresses/", "/ip-addresses/1/", "/dhcp-ranges/1/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_an_address(client_with_role, role):
    client = client_with_role(role)
    site, _subnet, _port = plan()

    response = client.post(
        "/ip-addresses/", {"address": "192.168.1.10", "site_id": site.id, "description": "Printer"}
    )
    assert response.status_code == 302
    address = list_ip_addresses(root())[0]

    client.post(
        f"/ip-addresses/{address.id}/", {"address": "192.168.1.11", "description": "Scanner"}
    )
    edited = get_ip_address(root(), address.id)
    assert (edited.address, edited.description) == ("192.168.1.11", "Scanner")

    assert client.post(f"/ip-addresses/{address.id}/delete/").status_code == 302
    assert list_ip_addresses(root()) == []


def test_viewer_sees_addresses_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site, subnet, port = plan()
    address = assign_ip_address(root(), port.id, "192.168.1.10")
    dhcp = create_dhcp_range(root(), subnet.id, "192.168.1.100", "192.168.1.200")

    for path in (
        "/ip-addresses/",
        f"/ip-addresses/{address.id}/",
        f"/dhcp-ranges/{dhcp.id}/",
        f"/subnets/{subnet.id}/",
        f"/interfaces/{port.id}/",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count("<form") == 1

    new_address = {"address": "192.168.1.11", "site_id": site.id}
    new_range = {"start": "192.168.1.1", "end": "192.168.1.2"}
    assert client.post("/ip-addresses/", new_address).status_code == 403
    assert client.post(f"/ip-addresses/{address.id}/", new_address).status_code == 403
    assert client.post(f"/ip-addresses/{address.id}/delete/").status_code == 403
    assert client.post(f"/ip-addresses/{address.id}/unassign/").status_code == 403
    assert client.post(f"/interfaces/{port.id}/ip-addresses/", new_address).status_code == 403
    assert client.post(f"/subnets/{subnet.id}/ip-addresses/", new_address).status_code == 403
    assert client.post(f"/subnets/{subnet.id}/dhcp-ranges/", new_range).status_code == 403
    assert client.post(f"/dhcp-ranges/{dhcp.id}/", new_range).status_code == 403
    assert client.post(f"/dhcp-ranges/{dhcp.id}/delete/").status_code == 403


def test_site_selection_is_hidden_while_only_one_site_exists(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, _subnet, _port = plan()

    page = client.get("/ip-addresses/").content.decode()

    assert '<select name="site_id"' not in page
    assert f'type="hidden" name="site_id" value="{site.id}"' in page


def test_invalid_address_shows_the_error_and_keeps_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, _subnet, _port = plan()

    response = client.post("/ip-addresses/", {"address": "172.16.0.1", "site_id": site.id})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert 'value="172.16.0.1"' in response.content.decode()


def test_interface_page_assigns_and_unassigns(client_with_role):
    client = client_with_role(Role.EDITOR)
    _site, _subnet, port = plan()

    response = client.post(f"/interfaces/{port.id}/ip-addresses/", {"address": "192.168.1.10"})
    assert response.status_code == 302
    assert response["Location"] == f"/interfaces/{port.id}/"
    address = list_ip_addresses(root())[0]
    assert address.interface_id == port.id

    page = client.get(f"/interfaces/{port.id}/").content.decode()
    assert f'<a href="/ip-addresses/{address.id}/">192.168.1.10</a>' in page

    response = client.post(f"/ip-addresses/{address.id}/unassign/")
    assert response.status_code == 302
    assert get_ip_address(root(), address.id).status == "reserved"


def test_invalid_assignment_shows_the_error_on_the_interface_page(client_with_role):
    client = client_with_role(Role.EDITOR)
    _site, _subnet, port = plan()

    response = client.post(f"/interfaces/{port.id}/ip-addresses/", {"address": "172.16.0.1"})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_device_page_shows_addresses_of_its_interfaces(client_with_role):
    client = client_with_role(Role.VIEWER)
    _site, _subnet, port = plan()
    address = assign_ip_address(root(), port.id, "192.168.1.10")

    page = client.get(f"/devices/{port.device_id}/").content.decode()

    assert f'<a href="/ip-addresses/{address.id}/">192.168.1.10</a>' in page


def test_subnet_page_shows_usage_and_next_free(client_with_role):
    client = client_with_role(Role.VIEWER)
    site, subnet, port = plan()
    assigned = assign_ip_address(root(), port.id, "192.168.1.1")
    create_ip_address(root(), address="192.168.1.2", site_id=site.id)
    dhcp = create_dhcp_range(root(), subnet.id, "192.168.1.100", "192.168.1.109")

    page = client.get(f"/subnets/{subnet.id}/").content.decode()

    assert 'data-usage="assigned">1<' in page
    assert 'data-usage="reserved">1<' in page
    assert 'data-usage="dhcp">10<' in page
    assert 'data-usage="free">242<' in page
    assert 'data-usage="next-free">192.168.1.3<' in page
    assert f'<a href="/ip-addresses/{assigned.id}/">192.168.1.1</a>' in page
    assert f'<a href="/dhcp-ranges/{dhcp.id}/">' in page


def test_subnet_page_reserves_the_next_free_address(client_with_role):
    client = client_with_role(Role.EDITOR)
    _site, subnet, _port = plan()

    page = client.get(f"/subnets/{subnet.id}/").content.decode()
    assert 'name="address" value="192.168.1.1"' in page

    response = client.post(f"/subnets/{subnet.id}/ip-addresses/", {"address": "192.168.1.1"})

    assert response.status_code == 302
    assert response["Location"] == f"/subnets/{subnet.id}/"
    assert list_ip_addresses(root())[0].status == "reserved"


def test_reserving_outside_the_subnet_is_refused(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, subnet, _port = plan()
    create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)

    response = client.post(f"/subnets/{subnet.id}/ip-addresses/", {"address": "10.0.0.1"})

    assert response.status_code == 400
    assert list_ip_addresses(root()) == []


def test_writer_creates_edits_and_deletes_a_range(client_with_role):
    client = client_with_role(Role.EDITOR)
    _site, subnet, _port = plan()

    response = client.post(
        f"/subnets/{subnet.id}/dhcp-ranges/",
        {"start": "192.168.1.100", "end": "192.168.1.200", "description": "Clients"},
    )
    assert response.status_code == 302
    dhcp = list_dhcp_ranges(root())[0]

    client.post(
        f"/dhcp-ranges/{dhcp.id}/",
        {"start": "192.168.1.100", "end": "192.168.1.150", "description": "Clients"},
    )
    assert get_dhcp_range(root(), dhcp.id).end == "192.168.1.150"

    response = client.post(f"/dhcp-ranges/{dhcp.id}/delete/")
    assert response.status_code == 302
    assert response["Location"] == f"/subnets/{subnet.id}/"
    assert list_dhcp_ranges(root()) == []


def test_invalid_range_shows_the_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    _site, subnet, _port = plan()

    response = client.post(
        f"/subnets/{subnet.id}/dhcp-ranges/", {"start": "192.168.1.200", "end": "192.168.1.100"}
    )

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_deleting_a_subnet_with_addresses_shows_why(client_with_role):
    client = client_with_role(Role.EDITOR)
    site, subnet, _port = plan()
    create_ip_address(root(), address="192.168.1.10", site_id=site.id)

    response = client.post(f"/subnets/{subnet.id}/delete/")

    assert response.status_code == 400
    assert "no longer be inside" in response.content.decode()


def test_item_views_show_history(client_with_role):
    client = client_with_role(Role.VIEWER)
    site, subnet, _port = plan()
    address = create_ip_address(root(), address="192.168.1.10", site_id=site.id)
    dhcp = create_dhcp_range(root(), subnet.id, "192.168.1.100", "192.168.1.200")

    for path in (f"/ip-addresses/{address.id}/", f"/dhcp-ranges/{dhcp.id}/"):
        assert 'data-history-action="created"' in client.get(path).content.decode()


def test_navigation_links_to_addresses(client_with_role):
    assert 'href="/ip-addresses/"' in client_with_role(Role.VIEWER).get("/").content.decode()
