import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.devices.services import (
    create_device_type,
    delete_device_type,
    get_device_type,
    list_device_types,
    update_device_type,
)
from netatlas.history.services import item_history

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def editor(admin):
    return create_user(admin, "editor", "another-long-password", Role.EDITOR)


def test_editor_creates_a_device_type_with_bulk_defined_interfaces(editor):
    created = create_device_type(
        editor,
        make="Acme",
        model_name="SW-3",
        rack_height=1,
        interfaces=[
            {"name": "port[1-3]", "kind": "physical"},
            {"name": "lo0", "kind": "virtual"},
        ],
    )

    listed = list_device_types(editor)[0]
    assert (listed.make, listed.model_name, listed.rack_height) == ("Acme", "SW-3", 1)
    assert listed.interfaces == [
        {"name": "port1", "kind": "physical"},
        {"name": "port2", "kind": "physical"},
        {"name": "port3", "kind": "physical"},
        {"name": "lo0", "kind": "virtual"},
    ]
    entry = item_history(editor, "device-type", created.id)[0]
    assert (entry.action, entry.actor, entry.item_label) == ("created", "editor", "Acme SW-3")


def test_a_device_type_may_take_no_rack_space(editor):
    pdu = create_device_type(editor, make="Acme", model_name="Vertical PDU", rack_height=0)

    assert pdu.rack_height == 0


@pytest.mark.parametrize(
    ("pattern", "names"),
    [
        ("port[08-10]", ["port08", "port09", "port10"]),
        ("Gi[1-2]/0/[1-2]", ["Gi1/0/1", "Gi1/0/2", "Gi2/0/1", "Gi2/0/2"]),
        ("eth-[0-1]", ["eth-0", "eth-1"]),
    ],
)
def test_bulk_patterns_expand_to_interface_names(editor, pattern, names):
    created = create_device_type(
        editor,
        make="Acme",
        model_name="SW",
        interfaces=[{"name": pattern, "kind": "physical"}],
    )

    assert [interface["name"] for interface in created.interfaces] == names


@pytest.mark.parametrize(
    "interfaces",
    [
        pytest.param([{"name": "port[5-1]", "kind": "physical"}], id="reversed-range"),
        pytest.param([{"name": "port[1-", "kind": "physical"}], id="unclosed-range"),
        pytest.param([{"name": "port[a-b]", "kind": "physical"}], id="non-numeric-range"),
        pytest.param([{"name": "port[1-5000]", "kind": "physical"}], id="too-many"),
        pytest.param(
            [{"name": "a[1-1000000]b[1-1000000]", "kind": "physical"}], id="huge-product"
        ),
        pytest.param([{"name": "", "kind": "physical"}], id="blank-name"),
        pytest.param([{"name": "port 1", "kind": "physical"}], id="space-in-name"),
        pytest.param([{"name": "x" * 65, "kind": "physical"}], id="long-name"),
        pytest.param([{"name": "port\u00001", "kind": "physical"}], id="nul-in-name"),
        pytest.param([{"name": "port1", "kind": "wifi"}], id="unknown-kind"),
        pytest.param([{"name": "port1"}], id="missing-kind"),
        pytest.param(["port1"], id="not-an-object"),
        pytest.param(
            [{"name": "port[1-2]", "kind": "physical"}, {"name": "port2", "kind": "virtual"}],
            id="duplicate-after-expansion",
        ),
    ],
)
def test_invalid_interface_lists_are_refused(editor, interfaces):
    with pytest.raises(ValidationError) as error:
        create_device_type(editor, make="Acme", model_name="SW", interfaces=interfaces)

    assert "interfaces" in error.value.message_dict
    assert list_device_types(editor) == []


def test_make_and_model_are_unique(editor):
    create_device_type(editor, make="Acme", model_name="SW")

    with pytest.raises(ValidationError):
        create_device_type(editor, make="Acme", model_name="SW")

    assert create_device_type(editor, make="Other", model_name="SW").make == "Other"


def test_editing_a_device_type_expands_patterns_and_records_the_change(editor):
    created = create_device_type(
        editor,
        make="Acme",
        model_name="SW",
        interfaces=[{"name": "port1", "kind": "physical"}],
    )

    updated = update_device_type(
        editor,
        created.id,
        rack_height=2,
        interfaces=[{"name": "port[1-2]", "kind": "physical"}],
    )

    assert updated.rack_height == 2
    assert [interface["name"] for interface in get_device_type(editor, created.id).interfaces] == [
        "port1",
        "port2",
    ]
    latest = item_history(editor, "device-type", created.id)[0]
    assert latest.action == "updated"
    assert {change.field for change in latest.changes} == {"rack_height", "interfaces"}


def test_editing_to_an_existing_make_and_model_is_refused(editor):
    create_device_type(editor, make="Acme", model_name="SW")
    other = create_device_type(editor, make="Acme", model_name="Router")

    with pytest.raises(ValidationError):
        update_device_type(editor, other.id, model_name="SW")

    assert get_device_type(editor, other.id).model_name == "Router"


def test_deleted_device_type_is_gone_but_keeps_its_history(editor):
    created = create_device_type(editor, make="Acme", model_name="SW")

    delete_device_type(editor, created.id)

    with pytest.raises(Http404):
        get_device_type(editor, created.id)
    assert item_history(editor, "device-type", created.id)[0].action == "deleted"


def test_viewer_reads_but_cannot_change_device_types(admin, editor):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    created = create_device_type(editor, make="Acme", model_name="SW")

    assert get_device_type(viewer, created.id).make == "Acme"
    assert [item.id for item in list_device_types(viewer)] == [created.id]
    with pytest.raises(PermissionDenied):
        create_device_type(viewer, make="Other", model_name="SW")
    with pytest.raises(PermissionDenied):
        update_device_type(viewer, created.id, make="Other")
    with pytest.raises(PermissionDenied):
        delete_device_type(viewer, created.id)
