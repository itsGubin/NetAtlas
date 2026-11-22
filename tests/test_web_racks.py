import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import (
    create_device,
    create_rack,
    get_device,
    get_rack,
    list_racks,
    place_device,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/racks/", "/racks/1/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_creates_edits_and_deletes_a_rack(client_with_role, role):
    client = client_with_role(role)
    site = create_site(root(), name="Home")

    response = client.post("/racks/", {"name": "R1", "site_id": site.id, "height": "42"})
    assert response.status_code == 302
    rack = list_racks(root())[0]

    client.post(f"/racks/{rack.id}/", {"name": "R2", "height": "24"})
    edited = get_rack(root(), rack.id)
    assert (edited.name, edited.height) == ("R2", 24)

    assert confirm_delete(client, f"/racks/{rack.id}/delete/").status_code == 302
    assert list_racks(root()) == []


def test_viewer_sees_racks_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)

    for path in ("/racks/", f"/racks/{rack.id}/", f"/devices/{device.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count('method="post"') == 1

    position = {"rack_id": rack.id, "unit": "1", "face": "front"}
    assert client.post("/racks/", {"name": "x", "site_id": site.id}).status_code == 403
    assert client.post(f"/racks/{rack.id}/", {"name": "x", "height": "4"}).status_code == 403
    assert client.post(f"/racks/{rack.id}/delete/").status_code == 403
    assert client.post(f"/devices/{device.id}/placement/", position).status_code == 403
    assert client.post(f"/devices/{device.id}/unrack/").status_code == 403


def test_site_selection_is_hidden_while_only_one_site_exists(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    page = client.get("/racks/").content.decode()
    assert '<select name="site_id"' not in page
    assert f'type="hidden" name="site_id" value="{site.id}"' in page


@pytest.mark.parametrize("height", ["abc", "0", "101", ""])
def test_invalid_rack_shows_the_error_and_keeps_the_input(client_with_role, height):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = client.post("/racks/", {"name": "kept", "site_id": site.id, "height": height})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert 'value="kept"' in response.content.decode()


def test_rack_page_draws_both_faces_with_spans_and_zero_height(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    server = create_device(root(), name="srv-1", kind="physical", site_id=site.id, rack_height=2)
    switch = create_device(root(), name="sw-1", kind="physical", site_id=site.id)
    pdu = create_device(root(), name="pdu-1", kind="physical", site_id=site.id, rack_height=0)
    place_device(root(), server.id, rack.id, unit=2, face="front")
    place_device(root(), switch.id, rack.id, unit=1, face="rear")
    place_device(root(), pdu.id, rack.id)

    page = client.get(f"/racks/{rack.id}/").content.decode()

    front = page[page.index('data-face="front"') : page.index('data-face="rear"')]
    rear = page[page.index('data-face="rear"') :]
    assert f'rowspan="2"' in front
    assert f'<a href="/devices/{server.id}/">srv-1</a>' in front
    assert "sw-1" not in front
    assert f'<a href="/devices/{switch.id}/">sw-1</a>' in rear
    assert f'<a href="/devices/{pdu.id}/">pdu-1</a>' in page
    assert front.count("<tr") == 5


def test_writer_places_moves_and_removes_a_device(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)

    response = client.post(
        f"/devices/{device.id}/placement/", {"rack_id": rack.id, "unit": "2", "face": "rear"}
    )
    assert response.status_code == 302
    assert response["Location"] == f"/devices/{device.id}/"
    placed = get_device(root(), device.id)
    assert (placed.rack_id, placed.rack_unit, placed.rack_face) == (rack.id, 2, "rear")

    page = client.get(f"/devices/{device.id}/").content.decode()
    assert f'<a href="/racks/{rack.id}/">R1</a>' in page

    assert client.post(f"/devices/{device.id}/unrack/").status_code == 302
    assert get_device(root(), device.id).rack_id is None


def test_zero_height_device_form_has_no_position_fields(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    pdu = create_device(root(), name="pdu", kind="physical", site_id=site.id, rack_height=0)

    page = client.get(f"/devices/{pdu.id}/").content.decode()
    assert 'name="rack_id"' in page
    assert 'name="unit"' not in page
    assert 'name="face"' not in page

    client.post(f"/devices/{pdu.id}/placement/", {"rack_id": rack.id})
    assert get_device(root(), pdu.id).rack_id == rack.id


def test_placement_form_offers_only_racks_of_the_devices_site(client_with_role):
    client = client_with_role(Role.EDITOR)
    home = create_site(root(), name="Home")
    office = create_site(root(), name="Office")
    local = create_rack(root(), name="R1", site_id=home.id, height=4)
    foreign = create_rack(root(), name="R9", site_id=office.id, height=4)
    device = create_device(root(), name="sw", kind="physical", site_id=home.id)

    page = client.get(f"/devices/{device.id}/").content.decode()
    form = page[page.index('name="rack_id"') :]
    form = form[: form.index("</select>")]

    assert f'<option value="{local.id}"' in form
    assert f'<option value="{foreign.id}"' not in form


def test_virtual_device_has_no_placement_form(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    create_rack(root(), name="R1", site_id=site.id, height=4)
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    vm = create_device(root(), name="vm", kind="virtual", parent_id=host.id)

    assert 'name="rack_id"' not in client.get(f"/devices/{vm.id}/").content.decode()


@pytest.mark.parametrize(
    "data",
    [
        {"rack_id": "{rack}", "unit": "1", "face": "front"},
        {"rack_id": "{rack}", "unit": "abc", "face": "front"},
        {"rack_id": "{rack}", "unit": "99", "face": "front"},
        {"rack_id": "{rack}", "unit": "2", "face": "side"},
        {"rack_id": "", "unit": "2", "face": "front"},
        {"rack_id": "abc", "unit": "2", "face": "front"},
    ],
)
def test_invalid_placement_shows_the_error(client_with_role, data):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    taken = create_device(root(), name="taken", kind="physical", site_id=site.id)
    place_device(root(), taken.id, rack.id, unit=1, face="front")
    device = create_device(root(), name="sw", kind="physical", site_id=site.id)
    values = {k: str(rack.id) if v == "{rack}" else v for k, v in data.items()}

    response = client.post(f"/devices/{device.id}/placement/", values)

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert get_device(root(), device.id).rack_id is None


def test_deleting_a_used_rack_shows_why(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)
    device = create_device(root(), name="sw-1", kind="physical", site_id=site.id)
    place_device(root(), device.id, rack.id, unit=1, face="front")

    response = client.post(f"/racks/{rack.id}/delete/")

    assert response.status_code == 400
    assert "Take these Devices out of the Rack first" in response.content.decode()


def test_rack_view_shows_its_history(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    rack = create_rack(root(), name="R1", site_id=site.id, height=4)

    page = client.get(f"/racks/{rack.id}/").content.decode()

    assert 'data-history-action="created"' in page


def test_navigation_links_to_racks(client_with_role):
    assert 'href="/racks/"' in client_with_role(Role.VIEWER).get("/").content.decode()
