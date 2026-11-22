import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.addressing.services import (
    create_subnet,
    create_vlan,
    get_subnet,
    get_vlan,
    list_vlans,
)
from netatlas.devices.services import (
    create_device,
    create_interface,
    get_interface,
    set_interface_vlans,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def new_port(site, name="switch"):
    device = create_device(root(), name=name, kind="physical", site_id=site.id)
    return create_interface(root(), device.id, name="p1", kind="physical")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/vlans/", "/vlans/1/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_vlan(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")

    response = client.post("/vlans/", {"number": "10", "name": "Guest", "site_id": site.id})
    assert response.status_code == 302
    vlan = list_vlans(root())[0]

    client.post(f"/vlans/{vlan.id}/", {"number": "20", "name": "Staff"})
    edited = get_vlan(root(), vlan.id)
    assert (edited.number, edited.name) == (20, "Staff")

    assert confirm_delete(client, f"/vlans/{vlan.id}/delete/").status_code == 302
    assert list_vlans(root()) == []


def test_viewer_sees_vlans_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id)
    port = new_port(site)

    for path in ("/vlans/", f"/vlans/{vlan.id}/", f"/interfaces/{port.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count('method="post"') == 1

    assert client.post("/vlans/", {"number": "20", "site_id": site.id}).status_code == 403
    assert client.post(f"/vlans/{vlan.id}/", {"number": "20"}).status_code == 403
    assert client.post(f"/vlans/{vlan.id}/delete/").status_code == 403
    response = client.post(f"/interfaces/{port.id}/vlans/", {"untagged_vlan_id": vlan.id})
    assert response.status_code == 403


def test_site_selection_is_hidden_while_only_one_site_exists(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    page = client.get("/vlans/").content.decode()

    assert '<select name="site_id"' not in page
    assert f'type="hidden" name="site_id" value="{site.id}"' in page


@pytest.mark.parametrize("number", ["abc", "0", "4095", ""])
def test_invalid_vlan_shows_the_error_and_keeps_the_input(client_with_role, number):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = client.post("/vlans/", {"number": number, "name": "kept", "site_id": site.id})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert 'value="kept"' in response.content.decode()


def test_vlan_page_lists_subnets_and_interfaces(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id, name="Guest")
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id, vlan_id=vlan.id)
    port = new_port(site)
    set_interface_vlans(root(), port.id, vlan.id, [])

    page = client.get(f"/vlans/{vlan.id}/").content.decode()

    assert f'<a href="/subnets/{subnet.id}/">10.0.0.0/24</a>' in page
    assert f'<a href="/interfaces/{port.id}/">switch p1</a>' in page
    assert "untagged" in page
    assert 'data-history-action="created"' in page


def test_deleting_a_used_vlan_shows_why(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id)
    set_interface_vlans(root(), new_port(site).id, vlan.id, [])

    response = client.post(f"/vlans/{vlan.id}/delete/")

    assert response.status_code == 400
    assert "still used by" in response.content.decode()


def test_subnet_form_binds_and_unbinds_a_vlan(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id, name="Guest")
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=site.id)

    page = client.get(f"/subnets/{subnet.id}/").content.decode()
    assert f'<option value="{vlan.id}"' in page

    data = {"cidr": "10.0.0.0/24", "site_id": site.id}
    client.post(f"/subnets/{subnet.id}/", {**data, "vlan_id": vlan.id})
    assert get_subnet(root(), subnet.id).vlan_id == vlan.id
    assert f'<a href="/vlans/{vlan.id}/">' in client.get(f"/subnets/{subnet.id}/").content.decode()

    client.post(f"/subnets/{subnet.id}/", {**data, "vlan_id": ""})
    assert get_subnet(root(), subnet.id).vlan_id is None


def test_new_subnet_can_be_bound_to_a_vlan(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    vlan = create_vlan(root(), number=10, site_id=site.id)

    client.post("/subnets/", {"cidr": "10.0.0.0/24", "site_id": site.id, "vlan_id": vlan.id})

    from netatlas.addressing.services import list_subnets

    assert list_subnets(root())[0].vlan_id == vlan.id


def test_subnet_form_offers_only_vlans_of_its_site(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    local = create_vlan(root(), number=10, site_id=home.id)
    foreign = create_vlan(root(), number=20, site_id=office.id)
    subnet = create_subnet(root(), cidr="10.0.0.0/24", site_id=home.id)

    page = client.get(f"/subnets/{subnet.id}/").content.decode()
    form = page[page.index('name="vlan_id"') :]
    form = form[: form.index("</select>")]

    assert f'<option value="{local.id}"' in form
    assert f'<option value="{foreign.id}"' not in form


def test_interface_form_sets_untagged_and_tagged_vlans(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    untagged = create_vlan(root(), number=10, site_id=site.id)
    first = create_vlan(root(), number=20, site_id=site.id)
    second = create_vlan(root(), number=30, site_id=site.id)
    port = new_port(site)

    response = client.post(
        f"/interfaces/{port.id}/vlans/",
        {"untagged_vlan_id": untagged.id, "tagged_vlan_ids": [first.id, second.id]},
    )

    assert response.status_code == 302
    stored = get_interface(root(), port.id)
    assert stored.untagged_vlan.id == untagged.id
    assert [v.number for v in stored.tagged_vlans] == [20, 30]

    page = client.get(f"/interfaces/{port.id}/").content.decode()
    assert f'<option value="{untagged.id}" selected>' in page
    assert f'value="{first.id}" checked' in page

    client.post(f"/interfaces/{port.id}/vlans/", {"untagged_vlan_id": ""})
    cleared = get_interface(root(), port.id)
    assert (cleared.untagged_vlan, cleared.tagged_vlans) == (None, [])


def test_interface_form_offers_only_vlans_of_the_devices_site(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    local = create_vlan(root(), number=10, site_id=home.id)
    foreign = create_vlan(root(), number=20, site_id=office.id)
    port = new_port(home)

    page = client.get(f"/interfaces/{port.id}/").content.decode()

    assert f'name="tagged_vlan_ids" value="{local.id}"' in page
    assert f'name="tagged_vlan_ids" value="{foreign.id}"' not in page


def test_lag_member_has_no_vlan_form(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_vlan(root(), number=10, site_id=site.id)
    port = new_port(site)
    lag = create_interface(root(), port.device_id, name="lag0", kind="lag")
    member = create_interface(
        root(), port.device_id, name="p2", kind="physical", lag_id=lag.id
    )

    assert 'name="untagged_vlan_id"' not in client.get(f"/interfaces/{member.id}/").content.decode()
    assert 'name="untagged_vlan_id"' in client.get(f"/interfaces/{lag.id}/").content.decode()


@pytest.mark.parametrize("value", ["abc", "999999"])
def test_invalid_interface_vlans_show_the_error(client_with_role, value):
    client = client_with_role(Role.EDITOR)
    port = new_port(create_site(root(), name="Home"))

    response = client.post(f"/interfaces/{port.id}/vlans/", {"tagged_vlan_ids": [value]})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_device_page_shows_interface_vlans(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    untagged = create_vlan(root(), number=10, site_id=site.id)
    tagged = create_vlan(root(), number=20, site_id=site.id)
    port = new_port(site)
    set_interface_vlans(root(), port.id, untagged.id, [tagged.id])

    page = client.get(f"/devices/{port.device_id}/").content.decode()

    assert f'<a href="/vlans/{untagged.id}/">10</a>' in page
    assert f'<a href="/vlans/{tagged.id}/">20</a>' in page


def test_navigation_links_to_vlans(client_with_role):
    assert 'href="/vlans/"' in client_with_role(Role.VIEWER).get("/").content.decode()
