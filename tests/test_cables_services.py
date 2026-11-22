import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_interface,
    delete_cable,
    delete_device,
    delete_interface,
    get_cable,
    get_interface,
    list_cables,
    pair_interfaces,
    trace_interface,
    unpair_interface,
    update_cable,
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
def site(editor):
    return create_site(editor, name="Home")


@pytest.fixture
def make_port(editor, site):
    devices = {}

    def make(device_name, port_name, kind="physical"):
        if device_name not in devices:
            devices[device_name] = create_device(
                editor, name=device_name, kind="physical", site_id=site.id
            )
        return create_interface(editor, devices[device_name].id, name=port_name, kind=kind)

    return make


def names(trace):
    return [(hop.device_name, hop.interface_name) for hop in trace.hops]


def test_cable_joins_two_physical_interfaces_and_shows_on_both(editor, make_port):
    switch_port = make_port("switch", "port12")
    server_port = make_port("server", "eth0")

    cable = create_cable(editor, switch_port.id, server_port.id, label="A-114")

    assert get_cable(editor, cable.id).label == "A-114"
    near = get_interface(editor, switch_port.id)
    far = get_interface(editor, server_port.id)
    assert (near.cable_id, near.peer_device_name, near.peer_interface_name) == (
        cable.id,
        "server",
        "eth0",
    )
    assert (far.cable_id, far.peer_device_name, far.peer_interface_name) == (
        cable.id,
        "switch",
        "port12",
    )
    assert item_history(editor, "cable", cable.id)[0].action == "created"


def test_interface_has_at_most_one_cable_on_either_end(editor, make_port):
    first = make_port("a", "p1")
    second = make_port("b", "p1")
    third = make_port("c", "p1")
    create_cable(editor, first.id, second.id)

    for left, right in ((first, third), (third, first), (second, third), (third, second)):
        with pytest.raises(ValidationError):
            create_cable(editor, left.id, right.id)

    assert len(list_cables(editor)) == 1


def test_a_cable_needs_two_different_interfaces(editor, make_port):
    port = make_port("a", "p1")

    with pytest.raises(ValidationError) as error:
        create_cable(editor, port.id, port.id)

    assert "interface_b_id" in error.value.message_dict


@pytest.mark.parametrize("kind", ["virtual", "lag"])
def test_only_physical_interfaces_take_a_cable(editor, make_port, kind):
    port = make_port("a", "p1")
    other = make_port("b", "x", kind=kind)

    with pytest.raises(ValidationError) as error:
        create_cable(editor, port.id, other.id)

    assert "interface_b_id" in error.value.message_dict


def test_unknown_interface_is_a_field_error(editor, make_port):
    port = make_port("a", "p1")

    with pytest.raises(ValidationError) as error:
        create_cable(editor, 999999, port.id)

    assert "interface_a_id" in error.value.message_dict


def test_cable_may_cross_sites(editor, make_port):
    office = create_site(editor, name="Office")
    remote = create_device(editor, name="remote", kind="physical", site_id=office.id)
    remote_port = create_interface(editor, remote.id, name="fiber", kind="physical")
    local_port = make_port("local", "fiber")

    create_cable(editor, local_port.id, remote_port.id)

    assert [hop.site_name for hop in trace_interface(editor, local_port.id).hops] == [
        "Home",
        "Office",
    ]


def test_label_can_be_changed_and_is_recorded(editor, make_port):
    cable = create_cable(editor, make_port("a", "p1").id, make_port("b", "p1").id)

    assert update_cable(editor, cable.id, label="B-7").label == "B-7"

    latest = item_history(editor, "cable", cable.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [("label", "", "B-7")]


def test_labels_are_trimmed_so_a_blank_one_stays_empty(editor, make_port):
    cable = create_cable(editor, make_port("a", "p1").id, make_port("b", "p1").id, label="   ")
    assert cable.label == ""

    assert update_cable(editor, cable.id, label="  A-1 ").label == "A-1"
    assert update_cable(editor, cable.id, label="   ").label == ""


def test_deleting_a_cable_frees_both_interfaces(editor, make_port):
    first = make_port("a", "p1")
    second = make_port("b", "p1")
    cable = create_cable(editor, first.id, second.id)

    delete_cable(editor, cable.id)

    with pytest.raises(Http404):
        get_cable(editor, cable.id)
    assert get_interface(editor, first.id).cable_id is None
    assert item_history(editor, "cable", cable.id)[0].action == "deleted"
    assert create_cable(editor, first.id, second.id).id


def test_deleting_an_interface_deletes_its_cable_and_records_it(editor, make_port):
    first = make_port("a", "p1")
    second = make_port("b", "p1")
    cable = create_cable(editor, first.id, second.id)

    delete_interface(editor, first.id)

    assert list_cables(editor) == []
    assert get_interface(editor, second.id).cable_id is None
    assert item_history(editor, "cable", cable.id)[0].action == "deleted"


def test_deleting_a_device_deletes_the_cables_on_its_interfaces(editor, site):
    first = create_device(editor, name="a", kind="physical", site_id=site.id)
    second = create_device(editor, name="b", kind="physical", site_id=site.id)
    near = create_interface(editor, first.id, name="p1", kind="physical")
    far = create_interface(editor, second.id, name="p1", kind="physical")
    cable = create_cable(editor, near.id, far.id)

    delete_device(editor, first.id)

    assert item_history(editor, "cable", cable.id)[0].action == "deleted"
    assert get_interface(editor, far.id).cable_id is None


def test_cables_filter_by_device(editor, site, make_port):
    a1 = make_port("a", "p1")
    b1 = make_port("b", "p1")
    c1 = make_port("c", "p1")
    d1 = make_port("d", "p1")
    first = create_cable(editor, a1.id, b1.id)
    create_cable(editor, c1.id, d1.id)

    assert [c.id for c in list_cables(editor, device_id=b1.device_id)] == [first.id]


def test_pair_links_two_physical_interfaces_of_one_device(editor, make_port):
    front = make_port("panel", "front1")
    rear = make_port("panel", "rear1")

    pair_interfaces(editor, front.id, rear.id)

    assert get_interface(editor, front.id).paired_interface_name == "rear1"
    assert get_interface(editor, rear.id).paired_interface_name == "front1"
    assert item_history(editor, "interface", rear.id)[0].action == "updated"


def test_unpairing_clears_both_sides(editor, make_port):
    front = make_port("panel", "front1")
    rear = make_port("panel", "rear1")
    pair_interfaces(editor, front.id, rear.id)

    unpair_interface(editor, rear.id)

    assert get_interface(editor, front.id).paired_interface_id is None
    assert get_interface(editor, rear.id).paired_interface_id is None


def test_pair_rules(editor, make_port):
    front = make_port("panel", "front1")
    rear = make_port("panel", "rear1")
    spare = make_port("panel", "spare")
    virtual = make_port("panel", "vlan1", kind="virtual")
    elsewhere = make_port("other", "p1")
    pair_interfaces(editor, front.id, rear.id)

    for left, right in (
        (spare, spare),
        (spare, virtual),
        (virtual, spare),
        (spare, elsewhere),
        (spare, front),
        (front, spare),
    ):
        with pytest.raises(ValidationError):
            pair_interfaces(editor, left.id, right.id)

    assert get_interface(editor, spare.id).paired_interface_id is None


def test_deleting_one_side_dissolves_the_pair_and_records_it(editor, make_port):
    front = make_port("panel", "front1")
    rear = make_port("panel", "rear1")
    pair_interfaces(editor, front.id, rear.id)

    delete_interface(editor, front.id)

    assert get_interface(editor, rear.id).paired_interface_id is None
    latest = item_history(editor, "interface", rear.id)[0]
    assert [(c.field, c.new) for c in latest.changes] == [("paired_with", None)]


def test_deleting_a_device_with_pairs_removes_them(editor, site):
    panel = create_device(editor, name="panel", kind="physical", site_id=site.id)
    front = create_interface(editor, panel.id, name="front", kind="physical")
    rear = create_interface(editor, panel.id, name="rear", kind="physical")
    pair_interfaces(editor, front.id, rear.id)

    delete_device(editor, panel.id)

    for interface in (front, rear):
        assert item_history(editor, "interface", interface.id)[0].action == "deleted"


@pytest.mark.parametrize("dependency", ["cable", "pair"])
def test_kind_is_locked_while_cabled_or_paired(editor, make_port, dependency):
    port = make_port("a", "p1")
    if dependency == "cable":
        create_cable(editor, port.id, make_port("b", "p1").id)
    else:
        pair_interfaces(editor, port.id, make_port("a", "p2").id)

    with pytest.raises(ValidationError) as error:
        update_interface(editor, port.id, kind="virtual")

    assert "kind" in error.value.message_dict


def test_uncabled_interface_traces_to_itself(editor, make_port):
    port = make_port("a", "p1")

    trace = trace_interface(editor, port.id)

    assert names(trace) == [("a", "p1")]
    assert trace.is_loop is False


@pytest.fixture
def patched_path(editor, make_port):
    """switch:p12 -A- panel1:front -pair- panel1:rear -B- panel2:rear -pair- panel2:front -C- pc"""
    switch = make_port("switch", "p12")
    front1 = make_port("panel1", "front")
    rear1 = make_port("panel1", "rear")
    rear2 = make_port("panel2", "rear")
    front2 = make_port("panel2", "front")
    pc = make_port("pc", "eth0")
    pair_interfaces(editor, front1.id, rear1.id)
    pair_interfaces(editor, front2.id, rear2.id)
    create_cable(editor, switch.id, front1.id, label="A")
    create_cable(editor, rear1.id, rear2.id, label="B")
    create_cable(editor, front2.id, pc.id, label="C")
    return switch, front1, rear1, rear2, front2, pc


FULL_PATH = [
    ("switch", "p12"),
    ("panel1", "front"),
    ("panel1", "rear"),
    ("panel2", "rear"),
    ("panel2", "front"),
    ("pc", "eth0"),
]


def test_trace_follows_cables_through_patch_panels(editor, patched_path):
    trace = trace_interface(editor, patched_path[0].id)

    assert names(trace) == FULL_PATH
    assert [hop.cable_label for hop in trace.hops] == ["A", None, "B", None, "C", None]
    assert [hop.is_start for hop in trace.hops] == [True, False, False, False, False, False]
    assert trace.is_loop is False


def test_trace_from_the_middle_shows_the_whole_path(editor, patched_path):
    start = patched_path[3]

    trace = trace_interface(editor, start.id)

    assert sorted(names(trace)) == sorted(FULL_PATH)
    assert names(trace) in (FULL_PATH, list(reversed(FULL_PATH)))
    assert [hop.interface_id for hop in trace.hops if hop.is_start] == [start.id]


def test_trace_stops_and_reports_a_loop(editor, make_port):
    a_front = make_port("a", "front")
    a_rear = make_port("a", "rear")
    b_front = make_port("b", "front")
    b_rear = make_port("b", "rear")
    pair_interfaces(editor, a_front.id, a_rear.id)
    pair_interfaces(editor, b_front.id, b_rear.id)
    create_cable(editor, a_rear.id, b_front.id)
    create_cable(editor, b_rear.id, a_front.id)

    trace = trace_interface(editor, a_front.id)

    assert trace.is_loop is True
    assert len(trace.hops) == 4


@pytest.mark.parametrize("kind", ["virtual", "lag"])
def test_only_physical_interfaces_can_be_traced(editor, make_port, kind):
    interface = make_port("a", "x", kind=kind)

    with pytest.raises(ValidationError):
        trace_interface(editor, interface.id)


def test_viewer_reads_but_cannot_change_cables(admin, editor, make_port):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    first = make_port("a", "p1")
    second = make_port("b", "p1")
    spare = make_port("a", "p2")
    cable = create_cable(editor, first.id, second.id)

    assert get_cable(viewer, cable.id).id == cable.id
    assert [c.id for c in list_cables(viewer)] == [cable.id]
    assert len(trace_interface(viewer, first.id).hops) == 2
    for action in (
        lambda: create_cable(viewer, spare.id, make_port("c", "p1").id),
        lambda: update_cable(viewer, cable.id, label="x"),
        lambda: delete_cable(viewer, cable.id),
        lambda: pair_interfaces(viewer, first.id, spare.id),
        lambda: unpair_interface(viewer, first.id),
    ):
        with pytest.raises(PermissionDenied):
            action()
