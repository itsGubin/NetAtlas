import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.devices.services import (
    create_device,
    create_device_type,
    delete_device,
    delete_device_type,
    get_device,
    list_devices,
    update_device,
    update_device_type,
    virtual_devices,
)
from netatlas.history.services import item_history
from netatlas.places.services import create_site, delete_site

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def editor(admin):
    return create_user(admin, "editor", "another-long-password", Role.EDITOR)


@pytest.fixture
def site(editor):
    return create_site(editor, name="Home")


def test_device_created_from_a_type_keeps_its_copy_when_the_type_changes(editor, site):
    switch = create_device_type(
        editor,
        make="Acme",
        model_name="SW-2",
        rack_height=2,
        interfaces=[{"name": "port[1-2]", "kind": "physical"}],
    )

    device = create_device(
        editor, name="core-1", kind="physical", site_id=site.id, device_type_id=switch.id
    )
    update_device_type(editor, switch.id, rack_height=4, interfaces=[])

    stored = get_device(editor, device.id)
    assert (stored.name, stored.kind, stored.site_id, stored.rack_height) == (
        "core-1",
        "physical",
        site.id,
        2,
    )
    assert [(i.name, i.kind) for i in stored.interfaces] == [
        ("port1", "physical"),
        ("port2", "physical"),
    ]
    assert item_history(editor, "device", device.id)[0].action == "created"
    assert item_history(editor, "interface", stored.interfaces[0].id)[0].action == "created"


@pytest.fixture
def host(editor, site):
    return create_device(editor, name="proxmox", kind="physical", site_id=site.id)


def virtual(editor, name, parent):
    return create_device(editor, name=name, kind="virtual", parent_id=parent.id)


def test_virtual_device_inherits_the_site_and_takes_no_rack_space(editor, site, host):
    vm = virtual(editor, "vm-1", host)

    assert (vm.site_id, vm.parent_id, vm.parent_name, vm.rack_height) == (
        site.id,
        host.id,
        "proxmox",
        0,
    )


def test_virtual_device_requires_a_hypervisor(editor, site):
    with pytest.raises(ValidationError) as error:
        create_device(editor, name="vm", kind="virtual", site_id=site.id)

    assert "parent_id" in error.value.message_dict


def test_physical_device_cannot_have_a_hypervisor(editor, site, host):
    with pytest.raises(ValidationError) as error:
        create_device(editor, name="box", kind="physical", site_id=site.id, parent_id=host.id)

    assert "parent_id" in error.value.message_dict


def test_virtual_device_cannot_name_a_different_site(editor, host):
    other = create_site(editor, name="Office")

    with pytest.raises(ValidationError) as error:
        create_device(editor, name="vm", kind="virtual", parent_id=host.id, site_id=other.id)

    assert "site_id" in error.value.message_dict


def test_virtual_device_can_use_a_purely_virtual_type(editor, host):
    template = create_device_type(
        editor,
        make="Acme",
        model_name="VM",
        rack_height=2,
        interfaces=[{"name": "eth[0-1]", "kind": "virtual"}],
    )

    vm = create_device(
        editor, name="vm", kind="virtual", parent_id=host.id, device_type_id=template.id
    )

    assert vm.rack_height == 0
    assert [i.name for i in vm.interfaces] == ["eth0", "eth1"]


@pytest.mark.parametrize("kind", ["physical", "lag"])
def test_virtual_device_refuses_a_type_with_hardware_interfaces(editor, host, kind):
    template = create_device_type(
        editor,
        make="Acme",
        model_name="SW",
        interfaces=[{"name": "eth0", "kind": "virtual"}, {"name": "p1", "kind": kind}],
    )

    with pytest.raises(ValidationError) as error:
        create_device(
            editor, name="vm", kind="virtual", parent_id=host.id, device_type_id=template.id
        )

    assert "device_type_id" in error.value.message_dict
    assert [d.name for d in list_devices(editor)] == ["proxmox"]


def test_unknown_references_are_field_errors(editor, site, host):
    with pytest.raises(ValidationError) as error:
        create_device(
            editor, name="x", kind="physical", site_id=site.id, device_type_id=999999
        )
    assert "device_type_id" in error.value.message_dict

    with pytest.raises(ValidationError) as error:
        create_device(editor, name="x", kind="virtual", parent_id=999999)
    assert "parent_id" in error.value.message_dict

    with pytest.raises(ValidationError) as error:
        create_device(editor, name="x", kind="physical", site_id=999999)
    assert "site" in error.value.message_dict


def test_device_names_are_unique_per_site(editor, site, host):
    other = create_site(editor, name="Office")

    with pytest.raises(ValidationError):
        create_device(editor, name="proxmox", kind="physical", site_id=site.id)

    assert create_device(editor, name="proxmox", kind="physical", site_id=other.id).id


def test_hypervisor_lists_its_virtual_devices_nested(editor, host):
    vm_b = virtual(editor, "vm-b", host)
    vm_a = virtual(editor, "vm-a", host)
    container = virtual(editor, "container", vm_b)

    nested = virtual_devices(editor, host.id)

    assert [(entry.depth, entry.id) for entry in nested] == [
        (1, vm_a.id),
        (1, vm_b.id),
        (2, container.id),
    ]


def test_a_device_cannot_become_its_own_ancestor(editor, host):
    vm = virtual(editor, "vm", host)
    container = virtual(editor, "container", vm)

    for parent in (vm, container):
        with pytest.raises(ValidationError) as error:
            update_device(editor, vm.id, parent_id=parent.id)
        assert "parent_id" in error.value.message_dict

    assert get_device(editor, vm.id).parent_id == host.id


def test_moving_a_hypervisor_moves_everything_nested_on_it(editor, host):
    office = create_site(editor, name="Office")
    vm = virtual(editor, "vm", host)
    container = virtual(editor, "container", vm)

    update_device(editor, host.id, site_id=office.id)

    assert {get_device(editor, d.id).site_id for d in (host, vm, container)} == {office.id}
    latest = item_history(editor, "device", container.id)[0]
    assert [change.field for change in latest.changes] == ["site"]


def test_a_name_clash_in_the_new_site_moves_nothing(editor, site, host):
    office = create_site(editor, name="Office")
    vm = virtual(editor, "taken", host)
    create_device(editor, name="taken", kind="physical", site_id=office.id)

    with pytest.raises(ValidationError):
        update_device(editor, host.id, site_id=office.id)

    assert get_device(editor, host.id).site_id == site.id
    assert get_device(editor, vm.id).site_id == site.id


def test_new_hypervisor_in_another_site_moves_the_virtual_device(editor, host):
    office = create_site(editor, name="Office")
    other_host = create_device(editor, name="esxi", kind="physical", site_id=office.id)
    vm = virtual(editor, "vm", host)
    container = virtual(editor, "container", vm)

    update_device(editor, vm.id, parent_id=other_host.id)

    assert get_device(editor, vm.id).site_id == office.id
    assert get_device(editor, container.id).site_id == office.id


def test_virtual_device_cannot_change_site_on_its_own(editor, host):
    office = create_site(editor, name="Office")
    vm = virtual(editor, "vm", host)

    with pytest.raises(ValidationError) as error:
        update_device(editor, vm.id, site_id=office.id)

    assert "site_id" in error.value.message_dict


def test_deleting_a_device_deletes_its_interfaces_and_records_both(editor, site):
    template = create_device_type(
        editor, make="Acme", model_name="SW", interfaces=[{"name": "p1", "kind": "physical"}]
    )
    device = create_device(
        editor, name="sw", kind="physical", site_id=site.id, device_type_id=template.id
    )

    delete_device(editor, device.id)

    with pytest.raises(Http404):
        get_device(editor, device.id)
    assert item_history(editor, "device", device.id)[0].action == "deleted"
    assert item_history(editor, "interface", device.interfaces[0].id)[0].action == "deleted"


def test_deleting_a_hypervisor_is_refused_and_names_its_virtual_devices(editor, host):
    virtual(editor, "vm-1", host)

    with pytest.raises(ValidationError) as error:
        delete_device(editor, host.id)

    assert "vm-1" in " ".join(error.value.messages)
    assert get_device(editor, host.id).name == "proxmox"


def test_a_hypervisor_can_be_deleted_once_it_is_empty(editor, host):
    vm = virtual(editor, "vm-1", host)

    delete_device(editor, vm.id)
    delete_device(editor, host.id)

    assert list_devices(editor) == []


def test_a_site_with_devices_cannot_be_deleted(editor, site, host):
    create_site(editor, name="Office")

    with pytest.raises(ValidationError):
        delete_site(editor, site.id)

    assert get_device(editor, host.id).site_name == "Home"


def test_deleting_a_device_type_keeps_devices_created_from_it(editor, site):
    template = create_device_type(
        editor, make="Acme", model_name="SW", interfaces=[{"name": "p1", "kind": "physical"}]
    )
    device = create_device(
        editor, name="sw", kind="physical", site_id=site.id, device_type_id=template.id
    )

    delete_device_type(editor, template.id)

    stored = get_device(editor, device.id)
    assert (stored.device_type_id, [i.name for i in stored.interfaces]) == (None, ["p1"])
    latest = item_history(editor, "device", device.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [
        ("device_type", template.id, None)
    ]


def test_viewer_reads_but_cannot_change_devices(admin, editor, site, host):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)

    assert get_device(viewer, host.id).name == "proxmox"
    assert [d.id for d in list_devices(viewer)] == [host.id]
    with pytest.raises(PermissionDenied):
        create_device(viewer, name="x", kind="physical", site_id=site.id)
    with pytest.raises(PermissionDenied):
        update_device(viewer, host.id, name="x")
    with pytest.raises(PermissionDenied):
        delete_device(viewer, host.id)


def test_hypervisor_rules_hold_on_update(editor, site, host):
    vm = virtual(editor, "vm", host)

    with pytest.raises(ValidationError) as error:
        update_device(editor, host.id, parent_id=vm.id)
    assert "parent_id" in error.value.message_dict

    with pytest.raises(ValidationError) as error:
        update_device(editor, vm.id, parent_id=None)
    assert "parent_id" in error.value.message_dict
