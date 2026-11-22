import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.devices.services import (
    create_device,
    create_rack,
    delete_device,
    delete_rack,
    get_device,
    get_rack,
    list_racks,
    place_device,
    rack_elevation,
    unrack_device,
    update_device,
    update_rack,
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


@pytest.fixture
def rack(editor, site):
    return create_rack(editor, name="R1", site_id=site.id, height=10)


@pytest.fixture
def device(editor, site):
    def make(name, height=1):
        return create_device(
            editor, name=name, kind="physical", site_id=site.id, rack_height=height
        )

    return make


def test_editor_creates_edits_and_deletes_a_rack(editor, site):
    created = create_rack(editor, name="R1", site_id=site.id, height=42)

    assert [(r.name, r.site_name, r.height) for r in list_racks(editor)] == [("R1", "Home", 42)]
    assert item_history(editor, "rack", created.id)[0].action == "created"

    assert update_rack(editor, created.id, name="R2", height=24).height == 24
    assert get_rack(editor, created.id).name == "R2"

    delete_rack(editor, created.id)
    with pytest.raises(Http404):
        get_rack(editor, created.id)
    assert item_history(editor, "rack", created.id)[0].action == "deleted"


def test_rack_names_are_unique_per_site(editor, site, rack):
    other = create_site(editor, name="Office")

    with pytest.raises(ValidationError):
        create_rack(editor, name="R1", site_id=site.id, height=10)

    assert create_rack(editor, name="R1", site_id=other.id, height=10).site_id == other.id


@pytest.mark.parametrize("height", [0, 101])
def test_rack_height_must_be_sensible(editor, site, height):
    with pytest.raises(ValidationError) as error:
        create_rack(editor, name="R", site_id=site.id, height=height)

    assert "height" in error.value.message_dict


def test_racks_filter_by_site(editor, site, rack):
    other = create_site(editor, name="Office")
    elsewhere = create_rack(editor, name="R9", site_id=other.id, height=10)

    assert [r.id for r in list_racks(editor, site_id=other.id)] == [elsewhere.id]


def test_device_is_placed_moved_and_removed_with_history(editor, rack, device):
    server = device("server", height=2)

    placed = place_device(editor, server.id, rack.id, unit=3, face="front")
    assert (placed.rack_id, placed.rack_name, placed.rack_unit, placed.rack_face) == (
        rack.id,
        "R1",
        3,
        "front",
    )

    moved = place_device(editor, server.id, rack.id, unit=5, face="rear")
    assert (moved.rack_unit, moved.rack_face) == (5, "rear")
    latest = item_history(editor, "device", server.id)[0]
    assert {change.field for change in latest.changes} == {"rack_unit", "rack_face"}

    removed = unrack_device(editor, server.id)
    assert (removed.rack_id, removed.rack_unit, removed.rack_face) == (None, None, "")


@pytest.mark.parametrize(
    ("unit", "face"),
    [(4, "front"), (5, "front"), (6, "front")],
)
def test_overlap_on_the_same_face_is_refused(editor, rack, device, unit, face):
    place_device(editor, device("a", height=2).id, rack.id, unit=5, face="front")
    other = device("b", height=2)

    with pytest.raises(ValidationError) as error:
        place_device(editor, other.id, rack.id, unit=unit, face=face)

    assert "unit" in error.value.message_dict
    assert "a" in " ".join(error.value.messages)
    assert get_device(editor, other.id).rack_id is None


def test_adjacent_units_and_the_other_face_are_free(editor, rack, device):
    place_device(editor, device("a", height=2).id, rack.id, unit=5, face="front")

    below = place_device(editor, device("below", height=2).id, rack.id, unit=3, face="front")
    above = place_device(editor, device("above").id, rack.id, unit=7, face="front")
    behind = place_device(editor, device("behind", height=2).id, rack.id, unit=5, face="rear")

    assert [d.rack_unit for d in (below, above, behind)] == [3, 7, 5]


def test_a_device_can_move_onto_units_it_already_occupies(editor, rack, device):
    server = device("server", height=3)
    place_device(editor, server.id, rack.id, unit=2, face="front")

    assert place_device(editor, server.id, rack.id, unit=3, face="front").rack_unit == 3


@pytest.mark.parametrize("unit", [0, -1, 10, 11])
def test_device_must_fit_inside_the_rack(editor, rack, device, unit):
    with pytest.raises(ValidationError) as error:
        place_device(editor, device("server", height=2).id, rack.id, unit=unit, face="front")

    assert "unit" in error.value.message_dict


def test_device_fits_exactly_at_the_top(editor, rack, device):
    server = device("s", height=2)

    assert place_device(editor, server.id, rack.id, unit=9, face="rear").rack_unit == 9


@pytest.mark.parametrize(
    ("unit", "face", "field"),
    [(None, "front", "unit"), (1, None, "face"), (1, "side", "face")],
)
def test_a_racked_device_needs_a_unit_and_a_face(editor, rack, device, unit, face, field):
    with pytest.raises(ValidationError) as error:
        place_device(editor, device("server").id, rack.id, unit=unit, face=face)

    assert field in error.value.message_dict


def test_zero_height_device_is_in_the_rack_without_a_position(editor, rack, device):
    first = place_device(editor, device("pdu-a", height=0).id, rack.id)
    second = place_device(editor, device("pdu-b", height=0).id, rack.id)

    assert (first.rack_id, first.rack_unit, first.rack_face) == (rack.id, None, "")
    assert second.rack_id == rack.id


@pytest.mark.parametrize(("unit", "face"), [(1, None), (None, "front"), (1, "front")])
def test_zero_height_device_cannot_have_a_position(editor, rack, device, unit, face):
    with pytest.raises(ValidationError):
        place_device(editor, device("pdu", height=0).id, rack.id, unit=unit, face=face)


def test_virtual_device_cannot_be_racked(editor, rack, device):
    vm = create_device(editor, name="vm", kind="virtual", parent_id=device("host").id)

    with pytest.raises(ValidationError):
        place_device(editor, vm.id, rack.id)


def test_rack_must_be_in_the_devices_site(editor, device):
    other = create_site(editor, name="Office")
    foreign = create_rack(editor, name="R9", site_id=other.id, height=10)

    with pytest.raises(ValidationError) as error:
        place_device(editor, device("server").id, foreign.id, unit=1, face="front")

    assert "rack_id" in error.value.message_dict


def test_unknown_rack_is_a_field_error(editor, device):
    with pytest.raises(ValidationError) as error:
        place_device(editor, device("server").id, 999999, unit=1, face="front")

    assert "rack_id" in error.value.message_dict


def test_a_racked_device_cannot_change_site(editor, site, rack, device):
    other = create_site(editor, name="Office")
    server = device("server")
    place_device(editor, server.id, rack.id, unit=1, face="front")

    with pytest.raises(ValidationError) as error:
        update_device(editor, server.id, site_id=other.id)

    assert "site_id" in error.value.message_dict
    assert get_device(editor, server.id).site_id == site.id

    unrack_device(editor, server.id)
    assert update_device(editor, server.id, site_id=other.id).site_id == other.id


def test_growing_a_racked_device_cannot_overlap_or_leave_the_rack(editor, rack, device):
    server = device("server")
    place_device(editor, server.id, rack.id, unit=5, face="front")
    place_device(editor, device("above").id, rack.id, unit=7, face="front")

    assert update_device(editor, server.id, rack_height=2).rack_height == 2
    with pytest.raises(ValidationError) as error:
        update_device(editor, server.id, rack_height=3)
    assert "rack_height" in error.value.message_dict

    top = device("top")
    place_device(editor, top.id, rack.id, unit=10, face="rear")
    with pytest.raises(ValidationError):
        update_device(editor, top.id, rack_height=2)


@pytest.mark.parametrize(("before", "after"), [(1, 0), (0, 1)])
def test_a_racked_device_cannot_switch_between_zero_and_real_height(
    editor, rack, device, before, after
):
    server = device("server", height=before)
    if before:
        place_device(editor, server.id, rack.id, unit=1, face="front")
    else:
        place_device(editor, server.id, rack.id)

    with pytest.raises(ValidationError) as error:
        update_device(editor, server.id, rack_height=after)

    assert "rack_height" in error.value.message_dict


def test_rack_cannot_shrink_below_its_highest_device(editor, rack, device):
    place_device(editor, device("server", height=2).id, rack.id, unit=7, face="rear")

    with pytest.raises(ValidationError) as error:
        update_rack(editor, rack.id, height=7)
    assert "height" in error.value.message_dict

    assert update_rack(editor, rack.id, height=8).height == 8


def test_rack_with_devices_cannot_be_deleted_and_names_them(editor, rack, device):
    server = device("server-1")
    place_device(editor, server.id, rack.id, unit=1, face="front")

    with pytest.raises(ValidationError) as error:
        delete_rack(editor, rack.id)
    assert "server-1" in " ".join(error.value.messages)

    delete_device(editor, server.id)
    delete_rack(editor, rack.id)
    assert list_racks(editor) == []


def test_site_with_a_rack_cannot_be_deleted(editor, site, rack):
    create_site(editor, name="Office")

    with pytest.raises(ValidationError):
        delete_site(editor, site.id)

    assert get_rack(editor, rack.id).site_id == site.id


def test_elevation_shows_both_faces_top_down_with_spans_and_zero_height(editor, site, device):
    small = create_rack(editor, name="S", site_id=site.id, height=4)
    server = device("server", height=2)
    switch = device("switch")
    pdu = device("pdu", height=0)
    place_device(editor, server.id, small.id, unit=2, face="front")
    place_device(editor, switch.id, small.id, unit=4, face="rear")
    place_device(editor, pdu.id, small.id)

    elevation = rack_elevation(editor, small.id)

    assert [(u.unit, u.device_name, u.span, u.is_covered) for u in elevation.front] == [
        (4, None, 1, False),
        (3, "server", 2, False),
        (2, "server", 2, True),
        (1, None, 1, False),
    ]
    assert [(u.unit, u.device_id) for u in elevation.rear if u.device_id] == [(4, switch.id)]
    assert [(d.id, d.name) for d in elevation.zero_height] == [(pdu.id, "pdu")]


def test_viewer_reads_but_cannot_change_racks(admin, editor, site, rack, device):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    server = device("server")

    assert get_rack(viewer, rack.id).name == "R1"
    assert [r.id for r in list_racks(viewer)] == [rack.id]
    assert len(rack_elevation(viewer, rack.id).front) == 10
    for action in (
        lambda: create_rack(viewer, name="R2", site_id=site.id, height=10),
        lambda: update_rack(viewer, rack.id, name="R2"),
        lambda: delete_rack(viewer, rack.id),
        lambda: place_device(viewer, server.id, rack.id, unit=1, face="front"),
        lambda: unrack_device(viewer, server.id),
    ):
        with pytest.raises(PermissionDenied):
            action()
