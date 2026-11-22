import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.devices.services import (
    create_device,
    create_interface,
    delete_device,
    delete_interface,
    get_device,
    get_interface,
    list_interfaces,
    update_interface,
)
from netatlas.history.services import item_history
from netatlas.places.services import create_site

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def editor(admin):
    return create_user(admin, "editor", "another-long-password", Role.EDITOR)


@pytest.fixture
def switch(editor):
    site = create_site(editor, name="Home")
    return create_device(editor, name="switch", kind="physical", site_id=site.id)


@pytest.fixture
def vm(editor, switch):
    return create_device(editor, name="vm", kind="virtual", parent_id=switch.id)


def test_editor_adds_edits_and_deletes_an_interface(editor, switch):
    created = create_interface(editor, switch.id, name="port1", kind="physical")

    assert [(i.name, i.kind) for i in list_interfaces(editor, switch.id)] == [
        ("port1", "physical")
    ]
    assert item_history(editor, "interface", created.id)[0].action == "created"

    renamed = update_interface(editor, created.id, name="uplink")
    assert (renamed.name, renamed.device_id) == ("uplink", switch.id)
    latest = item_history(editor, "interface", created.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [("name", "port1", "uplink")]

    delete_interface(editor, created.id)
    with pytest.raises(Http404):
        get_interface(editor, created.id)
    assert item_history(editor, "interface", created.id)[0].action == "deleted"


def test_interface_names_are_unique_per_device(editor, switch, vm):
    create_interface(editor, switch.id, name="eth0", kind="physical")

    with pytest.raises(ValidationError):
        create_interface(editor, switch.id, name="eth0", kind="virtual")

    assert create_interface(editor, vm.id, name="eth0", kind="virtual").device_id == vm.id


@pytest.mark.parametrize("kind", ["physical", "lag"])
def test_virtual_device_only_has_virtual_interfaces(editor, vm, kind):
    with pytest.raises(ValidationError) as error:
        create_interface(editor, vm.id, name="eth0", kind=kind)

    assert "kind" in error.value.message_dict
    assert list_interfaces(editor, vm.id) == []


@pytest.mark.parametrize("kind", ["physical", "lag"])
def test_virtual_interface_on_a_virtual_device_cannot_change_to_hardware(editor, vm, kind):
    interface = create_interface(editor, vm.id, name="eth0", kind="virtual")

    with pytest.raises(ValidationError) as error:
        update_interface(editor, interface.id, kind=kind)

    assert "kind" in error.value.message_dict


def test_physical_interfaces_join_a_lag_of_the_same_device(editor, switch):
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")

    member = create_interface(editor, switch.id, name="p1", kind="physical", lag_id=lag.id)
    other = create_interface(editor, switch.id, name="p2", kind="physical")
    joined = update_interface(editor, other.id, lag_id=lag.id)

    assert (member.lag_id, member.lag_name) == (lag.id, "lag0")
    assert joined.lag_id == lag.id
    assert update_interface(editor, other.id, lag_id=None).lag_id is None


@pytest.mark.parametrize("kind", ["virtual", "lag"])
def test_only_physical_interfaces_can_join_a_lag(editor, switch, kind):
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")

    with pytest.raises(ValidationError) as error:
        create_interface(editor, switch.id, name="x", kind=kind, lag_id=lag.id)

    assert "lag_id" in error.value.message_dict


def test_a_lag_must_be_a_lag_on_the_same_device(editor, switch):
    site = create_site(editor, name="Office")
    other_device = create_device(editor, name="other", kind="physical", site_id=site.id)
    foreign_lag = create_interface(editor, other_device.id, name="lag0", kind="lag")
    not_a_lag = create_interface(editor, switch.id, name="p0", kind="physical")
    member = create_interface(editor, switch.id, name="p1", kind="physical")

    for lag_id in (foreign_lag.id, not_a_lag.id, member.id, 999999):
        with pytest.raises(ValidationError) as error:
            update_interface(editor, member.id, lag_id=lag_id)
        assert "lag_id" in error.value.message_dict

    assert get_interface(editor, member.id).lag_id is None


def test_kind_can_be_corrected_while_nothing_depends_on_it(editor, switch):
    interface = create_interface(editor, switch.id, name="x", kind="physical")

    assert update_interface(editor, interface.id, kind="virtual").kind == "virtual"
    assert update_interface(editor, interface.id, kind="lag").kind == "lag"


def test_kind_is_locked_for_lag_members_and_lags_with_members(editor, switch):
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")
    member = create_interface(editor, switch.id, name="p1", kind="physical", lag_id=lag.id)

    for interface in (lag, member):
        with pytest.raises(ValidationError) as error:
            update_interface(editor, interface.id, kind="virtual")
        assert "kind" in error.value.message_dict

    assert update_interface(editor, member.id, name="p1-renamed").kind == "physical"


def test_deleting_a_lag_keeps_its_members_and_records_the_change(editor, switch):
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")
    member = create_interface(editor, switch.id, name="p1", kind="physical", lag_id=lag.id)

    delete_interface(editor, lag.id)

    assert get_interface(editor, member.id).lag_id is None
    latest = item_history(editor, "interface", member.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [("lag", lag.id, None)]


def test_deleting_a_device_with_a_lag_removes_all_its_interfaces(editor, switch):
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")
    member = create_interface(editor, switch.id, name="p1", kind="physical", lag_id=lag.id)

    delete_device(editor, switch.id)

    for interface in (lag, member):
        assert item_history(editor, "interface", interface.id)[0].action == "deleted"


def test_device_summary_shows_lag_membership(editor, switch):
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")
    create_interface(editor, switch.id, name="p1", kind="physical", lag_id=lag.id)

    interfaces = get_device(editor, switch.id).interfaces

    assert [(i.name, i.lag_name) for i in interfaces] == [("lag0", None), ("p1", "lag0")]


def test_unknown_device_or_interface_is_not_found(editor):
    with pytest.raises(Http404):
        create_interface(editor, 999999, name="x", kind="physical")
    with pytest.raises(Http404):
        update_interface(editor, 999999, name="x")
    with pytest.raises(Http404):
        delete_interface(editor, 999999)
    with pytest.raises(Http404):
        list_interfaces(editor, 999999)


def test_viewer_reads_but_cannot_change_interfaces(admin, editor, switch):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    interface = create_interface(editor, switch.id, name="p1", kind="physical")

    assert get_interface(viewer, interface.id).name == "p1"
    assert [i.id for i in list_interfaces(viewer, switch.id)] == [interface.id]
    with pytest.raises(PermissionDenied):
        create_interface(viewer, switch.id, name="p2", kind="physical")
    with pytest.raises(PermissionDenied):
        update_interface(viewer, interface.id, name="p2")
    with pytest.raises(PermissionDenied):
        delete_interface(viewer, interface.id)
