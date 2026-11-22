import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_interface,
    get_cable,
    get_interface,
    list_cables,
    pair_interfaces,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

WRITERS = [Role.ADMIN, Role.EDITOR]


def root():
    return authenticate(username="root", password="correct-horse-battery")


def two_ports():
    site = create_site(root(), name="Home")
    first = create_device(root(), name="switch", kind="physical", site_id=site.id)
    second = create_device(root(), name="server", kind="physical", site_id=site.id)
    return (
        create_interface(root(), first.id, name="port12", kind="physical"),
        create_interface(root(), second.id, name="eth0", kind="physical"),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/cables/", "/cables/1/", "/interfaces/1/trace/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("role", WRITERS)
def test_writer_connects_relabels_and_removes_a_cable(client_with_role, role):
    client = client_with_role(role)
    near, far = two_ports()

    response = client.post(
        f"/interfaces/{near.id}/cable/", {"peer_id": far.id, "label": "A-114"}
    )
    assert response.status_code == 302
    assert response["Location"] == f"/interfaces/{near.id}/"
    cable = list_cables(root())[0]
    assert cable.label == "A-114"

    client.post(f"/cables/{cable.id}/", {"label": "B-7"})
    assert get_cable(root(), cable.id).label == "B-7"

    assert confirm_delete(client, f"/cables/{cable.id}/delete/").status_code == 302
    assert list_cables(root()) == []


def test_device_and_interface_pages_show_what_is_connected(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    create_cable(root(), near.id, far.id)

    device_page = client.get(f"/devices/{near.device_id}/").content.decode()
    interface_page = client.get(f"/interfaces/{near.id}/").content.decode()

    for page in (device_page, interface_page):
        assert f'<a href="/interfaces/{far.id}/">server eth0</a>' in page


def test_cable_form_offers_only_free_physical_interfaces(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()
    free = create_interface(root(), far.device_id, name="eth1", kind="physical")
    virtual = create_interface(root(), far.device_id, name="lo0", kind="virtual")
    taken = create_interface(root(), far.device_id, name="eth2", kind="physical")
    create_cable(root(), far.id, taken.id)

    page = client.get(f"/interfaces/{near.id}/").content.decode()
    cable_form = page[page.index('name="peer_id"') :]
    cable_form = cable_form[: cable_form.index("</select>")]

    assert f'<option value="{free.id}"' in cable_form
    for excluded in (near, far, virtual, taken):
        assert f'<option value="{excluded.id}"' not in cable_form


def test_cabled_interface_offers_no_second_cable(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()
    create_cable(root(), near.id, far.id)

    assert 'name="peer_id"' not in client.get(f"/interfaces/{near.id}/").content.decode()


def test_virtual_interface_offers_no_cable_pair_or_trace(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, _ = two_ports()
    virtual = create_interface(root(), near.device_id, name="lo0", kind="virtual")

    page = client.get(f"/interfaces/{virtual.id}/").content.decode()

    assert 'name="peer_id"' not in page
    assert 'name="partner_id"' not in page
    assert f"/interfaces/{virtual.id}/trace/" not in page


def test_writer_pairs_and_unpairs_interfaces(client_with_role):
    client = client_with_role(Role.EDITOR)
    front, _ = two_ports()
    rear = create_interface(root(), front.device_id, name="rear", kind="physical")

    response = client.post(f"/interfaces/{front.id}/pair/", {"partner_id": rear.id})
    assert response.status_code == 302
    assert get_interface(root(), rear.id).paired_interface_id == front.id

    assert client.post(f"/interfaces/{front.id}/unpair/").status_code == 302
    assert get_interface(root(), rear.id).paired_interface_id is None


def test_pair_form_offers_only_free_ports_of_the_same_device(client_with_role):
    client = client_with_role(Role.EDITOR)
    front, far = two_ports()
    rear = create_interface(root(), front.device_id, name="rear", kind="physical")

    page = client.get(f"/interfaces/{front.id}/").content.decode()
    pair_form = page[page.index('name="partner_id"') :]
    pair_form = pair_form[: pair_form.index("</select>")]

    assert f'<option value="{rear.id}"' in pair_form
    assert f'<option value="{far.id}"' not in pair_form
    assert f'<option value="{front.id}"' not in pair_form


def test_invalid_cable_shows_the_error_on_the_interface_page(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()
    spare = create_interface(root(), near.device_id, name="spare", kind="physical")
    create_cable(root(), near.id, far.id)

    response = client.post(f"/interfaces/{spare.id}/cable/", {"peer_id": far.id})

    assert response.status_code == 400
    assert "already has a Cable" in response.content.decode()


@pytest.mark.parametrize("value", ["", "abc"])
def test_cable_without_a_valid_peer_is_a_form_error(client_with_role, value):
    client = client_with_role(Role.EDITOR)
    near, _ = two_ports()

    response = client.post(f"/interfaces/{near.id}/cable/", {"peer_id": value})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_invalid_pair_shows_the_error(client_with_role):
    client = client_with_role(Role.EDITOR)
    near, far = two_ports()

    response = client.post(f"/interfaces/{near.id}/pair/", {"partner_id": far.id})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()


def test_viewer_sees_cables_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    spare = create_interface(root(), near.device_id, name="spare", kind="physical")
    cable = create_cable(root(), near.id, far.id)

    for path in ("/cables/", f"/cables/{cable.id}/", f"/interfaces/{near.id}/trace/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count("<form") == 1

    assert client.post(f"/interfaces/{spare.id}/cable/", {"peer_id": far.id}).status_code == 403
    assert client.post(f"/cables/{cable.id}/", {"label": "x"}).status_code == 403
    assert client.post(f"/cables/{cable.id}/delete/").status_code == 403
    assert client.post(f"/interfaces/{spare.id}/pair/", {"partner_id": near.id}).status_code == 403
    assert client.post(f"/interfaces/{spare.id}/unpair/").status_code == 403


def test_trace_page_shows_the_full_path_in_order(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    rear = create_interface(root(), far.device_id, name="rear", kind="physical")
    pair_interfaces(root(), far.id, rear.id)
    create_cable(root(), near.id, far.id, label="A-114")

    page = client.get(f"/interfaces/{near.id}/trace/").content.decode()

    assert page.index("port12") < page.index("A-114") < page.index("eth0") < page.index(">rear<")
    assert 'data-trace-start="true"' in page


def test_trace_page_reports_a_loop(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    near_rear = create_interface(root(), near.device_id, name="r1", kind="physical")
    far_rear = create_interface(root(), far.device_id, name="r2", kind="physical")
    pair_interfaces(root(), near.id, near_rear.id)
    pair_interfaces(root(), far.id, far_rear.id)
    create_cable(root(), near.id, far.id)
    create_cable(root(), near_rear.id, far_rear.id)

    assert 'role="status"' in client.get(f"/interfaces/{near.id}/trace/").content.decode()


def test_tracing_a_virtual_interface_is_not_found(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, _ = two_ports()
    virtual = create_interface(root(), near.device_id, name="lo0", kind="virtual")

    assert client.get(f"/interfaces/{virtual.id}/trace/").status_code == 404


def test_cable_view_shows_its_history_and_both_ends(client_with_role):
    client = client_with_role(Role.VIEWER)
    near, far = two_ports()
    cable = create_cable(root(), near.id, far.id)

    page = client.get(f"/cables/{cable.id}/").content.decode()

    assert 'data-history-action="created"' in page
    assert f'href="/interfaces/{near.id}/"' in page
    assert f'href="/interfaces/{far.id}/"' in page


def test_navigation_links_to_cables(client_with_role):
    assert 'href="/cables/"' in client_with_role(Role.VIEWER).get("/").content.decode()
