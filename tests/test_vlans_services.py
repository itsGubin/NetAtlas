import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.addressing.models import Vlan
from netatlas.addressing.services import (
    create_subnet,
    create_vlan,
    delete_subnet,
    delete_vlan,
    get_subnet,
    get_vlan,
    list_vlans,
    update_subnet,
    update_vlan,
    vlan_usage,
)
from netatlas.devices.models import Interface
from netatlas.devices.services import (
    create_device,
    create_interface,
    delete_interface,
    get_interface,
    set_interface_vlans,
    update_device,
    update_interface,
)
from netatlas.history.models import UntrackedWrite
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


@pytest.fixture
def office(editor):
    return create_site(editor, name="Office")


@pytest.fixture
def vlan(editor, site):
    def make(number, name=""):
        return create_vlan(editor, number=number, site_id=site.id, name=name)

    return make


@pytest.fixture
def switch(editor, site):
    return create_device(editor, name="switch", kind="physical", site_id=site.id)


@pytest.fixture
def port(editor, switch):
    return create_interface(editor, switch.id, name="p1", kind="physical")


def tagged(interface):
    return [v.number for v in interface.tagged_vlans]


def test_editor_creates_edits_and_deletes_a_vlan(editor, site):
    created = create_vlan(editor, number=10, site_id=site.id, name="  Guest ")

    assert (created.number, created.name, created.site_name) == (10, "Guest", "Home")
    assert item_history(editor, "vlan", created.id)[0].action == "created"

    updated = update_vlan(editor, created.id, number=20, name="Staff")
    assert (updated.number, updated.name) == (20, "Staff")
    assert get_vlan(editor, created.id).number == 20

    delete_vlan(editor, created.id)
    with pytest.raises(Http404):
        get_vlan(editor, created.id)
    assert item_history(editor, "vlan", created.id)[0].action == "deleted"


@pytest.mark.parametrize("number", [0, 4095, -1])
def test_vlan_number_must_be_valid(editor, site, number):
    with pytest.raises(ValidationError) as error:
        create_vlan(editor, number=number, site_id=site.id)

    assert "number" in error.value.message_dict


@pytest.mark.parametrize("number", [1, 4094])
def test_vlan_number_limits_are_allowed(editor, site, number):
    assert create_vlan(editor, number=number, site_id=site.id).number == number


def test_vlan_number_is_unique_per_site(editor, site, office, vlan):
    vlan(10)

    with pytest.raises(ValidationError):
        create_vlan(editor, number=10, site_id=site.id)

    assert create_vlan(editor, number=10, site_id=office.id).site_id == office.id


def test_vlans_list_in_number_order_and_filter_by_site(editor, office, vlan):
    vlan(20)
    vlan(3)
    elsewhere = create_vlan(editor, number=7, site_id=office.id)

    assert [v.number for v in list_vlans(editor, site_id=vlan(30).site_id)] == [3, 20, 30]
    assert [v.id for v in list_vlans(editor, site_id=office.id)] == [elsewhere.id]


def test_subnet_is_bound_to_a_vlan_of_its_site(editor, site, vlan):
    guest = vlan(10, "Guest")

    created = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=guest.id)
    assert (created.vlan_id, created.vlan_number, created.vlan_name) == (guest.id, 10, "Guest")

    assert update_subnet(editor, created.id, vlan_id=None).vlan_id is None
    assert update_subnet(editor, created.id, vlan_id=guest.id).vlan_id == guest.id


def test_subnet_cannot_bind_a_vlan_of_another_site(editor, site, office):
    foreign = create_vlan(editor, number=10, site_id=office.id)

    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=foreign.id)
    assert "vlan_id" in error.value.message_dict

    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    with pytest.raises(ValidationError) as error:
        update_subnet(editor, subnet.id, vlan_id=foreign.id)
    assert "vlan_id" in error.value.message_dict


def test_unknown_vlan_is_a_field_error(editor, site):
    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=999999)

    assert "vlan_id" in error.value.message_dict


def test_bound_subnet_cannot_change_site_until_unbound(editor, site, office, vlan):
    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=vlan(10).id)

    with pytest.raises(ValidationError) as error:
        update_subnet(editor, subnet.id, site_id=office.id)
    assert "site_id" in error.value.message_dict
    assert get_subnet(editor, subnet.id).site_id == site.id

    moved = update_subnet(editor, subnet.id, site_id=office.id, vlan_id=None)
    assert (moved.site_id, moved.vlan_id) == (office.id, None)


def test_subnet_can_move_and_bind_a_vlan_of_the_new_site_at_once(editor, site, office, vlan):
    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=vlan(10).id)
    target = create_vlan(editor, number=10, site_id=office.id)

    moved = update_subnet(editor, subnet.id, site_id=office.id, vlan_id=target.id)

    assert (moved.site_id, moved.vlan_id) == (office.id, target.id)


def test_interface_carries_one_untagged_and_many_tagged_vlans(editor, port, vlan):
    untagged = vlan(10)
    first = vlan(20)
    second = vlan(30)

    updated = set_interface_vlans(editor, port.id, untagged.id, [second.id, first.id])

    assert updated.untagged_vlan.number == 10
    assert tagged(updated) == [20, 30]
    stored = get_interface(editor, port.id)
    assert (stored.untagged_vlan.id, tagged(stored)) == (untagged.id, [20, 30])


def test_setting_vlans_replaces_the_previous_ones(editor, port, vlan):
    first = vlan(20)
    second = vlan(30)
    set_interface_vlans(editor, port.id, first.id, [second.id])

    updated = set_interface_vlans(editor, port.id, None, [first.id])

    assert (updated.untagged_vlan, tagged(updated)) == (None, [20])


def test_tagged_vlan_changes_are_recorded_in_history(editor, port, vlan):
    first = vlan(20)
    second = vlan(30)

    set_interface_vlans(editor, port.id, None, [first.id])
    set_interface_vlans(editor, port.id, None, [second.id, first.id])

    latest = item_history(editor, "interface", port.id)[0]
    assert (latest.action, latest.actor) == ("updated", "editor")
    assert [(c.field, c.old, c.new) for c in latest.changes] == [
        ("tagged_vlans", [first.id], sorted([first.id, second.id]))
    ]


def test_untagged_and_tagged_changes_are_one_history_entry(editor, port, vlan):
    first = vlan(20)
    second = vlan(30)

    set_interface_vlans(editor, port.id, first.id, [second.id])

    entries = [e for e in item_history(editor, "interface", port.id) if e.action == "updated"]
    assert len(entries) == 1
    assert {c.field for c in entries[0].changes} == {"untagged_vlan", "tagged_vlans"}


def test_setting_the_same_vlans_records_nothing(editor, port, vlan):
    first = vlan(20)
    set_interface_vlans(editor, port.id, first.id, [])
    before = len(item_history(editor, "interface", port.id))

    set_interface_vlans(editor, port.id, first.id, [])

    assert len(item_history(editor, "interface", port.id)) == before


def test_deleting_an_interface_records_its_tagged_vlans(editor, port, vlan):
    first = vlan(20)
    set_interface_vlans(editor, port.id, None, [first.id])

    delete_interface(editor, port.id)

    deleted = item_history(editor, "interface", port.id)[0]
    assert ("tagged_vlans", [first.id], None) in [(c.field, c.old, c.new) for c in deleted.changes]


def test_many_to_many_writes_outside_the_services_are_refused(editor, port, vlan):
    first = vlan(20)
    interface = Interface.objects.get(pk=port.id)

    with pytest.raises(UntrackedWrite):
        interface.tagged_vlans.add(Vlan.objects.get(pk=first.id))


def test_a_vlan_cannot_be_both_untagged_and_tagged(editor, port, vlan):
    first = vlan(20)

    with pytest.raises(ValidationError) as error:
        set_interface_vlans(editor, port.id, first.id, [first.id])

    assert "tagged_vlan_ids" in error.value.message_dict


def test_interface_vlans_must_be_in_the_devices_site(editor, port, office):
    foreign = create_vlan(editor, number=10, site_id=office.id)

    with pytest.raises(ValidationError) as error:
        set_interface_vlans(editor, port.id, foreign.id, [])
    assert "untagged_vlan_id" in error.value.message_dict

    with pytest.raises(ValidationError) as error:
        set_interface_vlans(editor, port.id, None, [foreign.id])
    assert "tagged_vlan_ids" in error.value.message_dict


def test_unknown_interface_vlans_are_field_errors(editor, port):
    with pytest.raises(ValidationError) as error:
        set_interface_vlans(editor, port.id, 999999, [])
    assert "untagged_vlan_id" in error.value.message_dict

    with pytest.raises(ValidationError) as error:
        set_interface_vlans(editor, port.id, None, [999999])
    assert "tagged_vlan_ids" in error.value.message_dict


def test_failed_vlan_change_keeps_the_previous_vlans(editor, port, vlan, office):
    first = vlan(20)
    foreign = create_vlan(editor, number=30, site_id=office.id)
    set_interface_vlans(editor, port.id, None, [first.id])

    with pytest.raises(ValidationError):
        set_interface_vlans(editor, port.id, None, [first.id, foreign.id])

    assert tagged(get_interface(editor, port.id)) == [20]


def test_lag_member_cannot_carry_vlans_but_the_lag_can(editor, switch, vlan):
    first = vlan(20)
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")
    member = create_interface(editor, switch.id, name="p1", kind="physical", lag_id=lag.id)

    with pytest.raises(ValidationError):
        set_interface_vlans(editor, member.id, first.id, [])
    with pytest.raises(ValidationError):
        set_interface_vlans(editor, member.id, None, [first.id])

    assert set_interface_vlans(editor, lag.id, first.id, []).untagged_vlan.id == first.id
    assert set_interface_vlans(editor, member.id, None, []).untagged_vlan is None


@pytest.mark.parametrize("mode", ["untagged", "tagged"])
def test_interface_with_vlans_cannot_join_a_lag(editor, switch, port, vlan, mode):
    first = vlan(20)
    lag = create_interface(editor, switch.id, name="lag0", kind="lag")
    if mode == "untagged":
        set_interface_vlans(editor, port.id, first.id, [])
    else:
        set_interface_vlans(editor, port.id, None, [first.id])

    with pytest.raises(ValidationError) as error:
        update_interface(editor, port.id, lag_id=lag.id)
    assert "lag_id" in error.value.message_dict

    set_interface_vlans(editor, port.id, None, [])
    assert update_interface(editor, port.id, lag_id=lag.id).lag_id == lag.id


@pytest.mark.parametrize("mode", ["untagged", "tagged"])
def test_device_with_vlans_cannot_change_site(editor, site, office, switch, port, vlan, mode):
    first = vlan(20)
    if mode == "untagged":
        set_interface_vlans(editor, port.id, first.id, [])
    else:
        set_interface_vlans(editor, port.id, None, [first.id])

    with pytest.raises(ValidationError) as error:
        update_device(editor, switch.id, site_id=office.id)
    assert "site_id" in error.value.message_dict

    set_interface_vlans(editor, port.id, None, [])
    assert update_device(editor, switch.id, site_id=office.id).site_id == office.id


def test_hypervisor_cannot_move_while_a_nested_device_uses_vlans(editor, office, switch, vlan):
    vm = create_device(editor, name="vm", kind="virtual", parent_id=switch.id)
    eth = create_interface(editor, vm.id, name="eth0", kind="virtual")
    set_interface_vlans(editor, eth.id, vlan(20).id, [])

    with pytest.raises(ValidationError) as error:
        update_device(editor, switch.id, site_id=office.id)

    assert "site_id" in error.value.message_dict


def test_virtual_device_with_vlans_cannot_move_to_a_hypervisor_elsewhere(
    editor, office, switch, vlan
):
    remote = create_device(editor, name="remote", kind="physical", site_id=office.id)
    vm = create_device(editor, name="vm", kind="virtual", parent_id=switch.id)
    eth = create_interface(editor, vm.id, name="eth0", kind="virtual")
    set_interface_vlans(editor, eth.id, vlan(20).id, [])

    with pytest.raises(ValidationError):
        update_device(editor, vm.id, parent_id=remote.id)


def test_device_with_vlans_can_change_hypervisor_within_its_site(editor, site, switch, vlan):
    other = create_device(editor, name="other", kind="physical", site_id=site.id)
    vm = create_device(editor, name="vm", kind="virtual", parent_id=switch.id)
    eth = create_interface(editor, vm.id, name="eth0", kind="virtual")
    set_interface_vlans(editor, eth.id, vlan(20).id, [])

    assert update_device(editor, vm.id, parent_id=other.id).parent_id == other.id


def test_usage_lists_subnets_and_interfaces(editor, site, switch, port, vlan):
    first = vlan(20)
    other_port = create_interface(editor, switch.id, name="p2", kind="physical")
    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=first.id)
    set_interface_vlans(editor, port.id, first.id, [])
    set_interface_vlans(editor, other_port.id, None, [first.id])

    usage = vlan_usage(editor, first.id)

    assert [(s.id, s.cidr) for s in usage.subnets] == [(subnet.id, "10.0.0.0/24")]
    assert [(i.device_name, i.interface_name, i.mode) for i in usage.interfaces] == [
        ("switch", "p1", "untagged"),
        ("switch", "p2", "tagged"),
    ]


@pytest.mark.parametrize("use", ["subnet", "untagged", "tagged"])
def test_vlan_in_use_cannot_be_deleted(editor, site, port, vlan, use):
    first = vlan(20)
    if use == "subnet":
        subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id, vlan_id=first.id)
    elif use == "untagged":
        set_interface_vlans(editor, port.id, first.id, [])
    else:
        set_interface_vlans(editor, port.id, None, [first.id])

    with pytest.raises(ValidationError) as error:
        delete_vlan(editor, first.id)
    assert "1" in " ".join(error.value.messages)
    assert get_vlan(editor, first.id).number == 20

    if use == "subnet":
        delete_subnet(editor, subnet.id)
    else:
        set_interface_vlans(editor, port.id, None, [])
    delete_vlan(editor, first.id)


def test_site_with_a_vlan_cannot_be_deleted(editor, site, office, vlan):
    first = vlan(20)

    with pytest.raises(ValidationError):
        delete_site(editor, site.id)

    assert get_vlan(editor, first.id).site_id == site.id


def test_viewer_reads_but_cannot_change_vlans(admin, editor, site, port, vlan):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    first = vlan(20)

    assert get_vlan(viewer, first.id).number == 20
    assert [v.id for v in list_vlans(viewer)] == [first.id]
    assert vlan_usage(viewer, first.id).interfaces == []
    for action in (
        lambda: create_vlan(viewer, number=30, site_id=site.id),
        lambda: update_vlan(viewer, first.id, name="x"),
        lambda: delete_vlan(viewer, first.id),
        lambda: set_interface_vlans(viewer, port.id, first.id, []),
    ):
        with pytest.raises(PermissionDenied):
            action()
