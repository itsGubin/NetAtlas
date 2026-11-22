import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.addressing.services import (
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    create_vlan,
)
from netatlas.deletion.services import confirm_delete, preview_delete
from netatlas.devices.models import Device
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
    delete_device,
    list_devices,
)
from netatlas.history.models import UntrackedWrite
from netatlas.history.services import item_history
from netatlas.places.services import create_customer, create_site
from netatlas.tagging.models import Tag
from netatlas.tagging.services import (
    TAGGABLE_KINDS,
    add_tag,
    create_tag,
    filter_by_tags,
    get_tag,
    list_tags,
    remove_tag,
    tag_usage,
    tags_of,
    update_tag,
)

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
def device(editor, site):
    return create_device(editor, name="server", kind="physical", site_id=site.id)


def names(actor, kind, item_id):
    return [tag.name for tag in tags_of(actor, kind, item_id)]


def one_of_each(editor, site, device):
    first = create_interface(editor, device.id, name="eth0", kind="physical")
    second = create_interface(editor, device.id, name="eth1", kind="physical")
    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    return {
        "customer": create_customer(editor, name="Acme").id,
        "site": site.id,
        "device": device.id,
        "interface": first.id,
        "rack": create_rack(editor, name="R1", site_id=site.id, height=4).id,
        "vlan": create_vlan(editor, number=10, site_id=site.id).id,
        "subnet": subnet.id,
        "dhcp-range": create_dhcp_range(editor, subnet.id, "10.0.0.100", "10.0.0.110").id,
        "ip-address": create_ip_address(editor, address="10.0.0.5", site_id=site.id).id,
        "cable": create_cable(editor, first.id, second.id).id,
        "device-type": create_device_type(editor, make="Acme", model_name="SW").id,
    }


def test_every_kind_of_documented_item_can_be_tagged(editor, site, device):
    items = one_of_each(editor, site, device)
    assert set(items) == set(TAGGABLE_KINDS)

    for kind, item_id in items.items():
        add_tag(editor, kind, item_id, "prod")
        assert names(editor, kind, item_id) == ["prod"]
        latest = item_history(editor, kind, item_id)[0]
        assert (latest.action, [c.field for c in latest.changes]) == ("updated", ["tags"])

        remove_tag(editor, kind, item_id, tags_of(editor, kind, item_id)[0].id)
        assert names(editor, kind, item_id) == []


def test_adding_and_removing_is_recorded_with_old_and_new(editor, device):
    first = add_tag(editor, "device", device.id, "prod")
    second = add_tag(editor, "device", device.id, "dmz")

    latest = item_history(editor, "device", device.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [
        ("tags", [first.id], sorted([first.id, second.id]))
    ]

    remove_tag(editor, "device", device.id, first.id)
    latest = item_history(editor, "device", device.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [
        ("tags", sorted([first.id, second.id]), [second.id])
    ]


def test_tags_list_alphabetically_on_an_item(editor, device):
    for name in ("web", "DMZ", "prod"):
        add_tag(editor, "device", device.id, name)

    assert names(editor, "device", device.id) == ["DMZ", "prod", "web"]


def test_spelling_variants_are_one_tag_keeping_the_first(editor, site, device):
    other = create_device(editor, name="other", kind="physical", site_id=site.id)
    first = add_tag(editor, "device", device.id, "  Prod ")

    second = add_tag(editor, "device", other.id, "pROD")

    assert second.id == first.id
    assert names(editor, "device", other.id) == ["Prod"]
    assert [tag.name for tag in list_tags(editor)] == ["Prod"]


def test_adding_a_tag_twice_records_nothing(editor, device):
    add_tag(editor, "device", device.id, "prod")
    before = len(item_history(editor, "device", device.id))

    add_tag(editor, "device", device.id, "PROD")

    assert len(item_history(editor, "device", device.id)) == before


@pytest.mark.parametrize("name", ["", "   ", "x" * 51, "a\x00b"])
def test_invalid_tag_name_is_a_field_error(editor, device, name):
    with pytest.raises(ValidationError) as error:
        add_tag(editor, "device", device.id, name)

    assert "name" in error.value.message_dict
    assert list_tags(editor) == []


def test_tag_can_be_created_and_renamed(editor):
    tag = create_tag(editor, "prod")

    assert update_tag(editor, tag.id, name="Production").name == "Production"
    assert get_tag(editor, tag.id).name == "Production"
    assert item_history(editor, "tag", tag.id)[0].action == "updated"


def test_changing_only_the_case_of_a_tag_is_allowed(editor):
    tag = create_tag(editor, "dmz")

    assert update_tag(editor, tag.id, name="DMZ").name == "DMZ"


def test_duplicate_spellings_are_refused(editor):
    create_tag(editor, "prod")
    other = create_tag(editor, "dev")

    with pytest.raises(ValidationError) as error:
        create_tag(editor, "PROD")
    assert "name" in error.value.message_dict

    with pytest.raises(ValidationError) as error:
        update_tag(editor, other.id, name="Prod")
    assert "name" in error.value.message_dict


def test_list_counts_usage_across_kinds(editor, site, device):
    add_tag(editor, "device", device.id, "prod")
    add_tag(editor, "site", site.id, "prod")
    create_tag(editor, "unused")

    assert [(tag.name, tag.usage) for tag in list_tags(editor)] == [("prod", 2), ("unused", 0)]


def test_usage_lists_the_tagged_items(editor, site, device):
    tag = add_tag(editor, "device", device.id, "prod")
    add_tag(editor, "site", site.id, "prod")

    usage = tag_usage(editor, tag.id)

    assert usage.tag.name == "prod"
    assert [(i.kind, i.id, i.label) for i in usage.items] == [
        ("device", device.id, "server"),
        ("site", site.id, "Home"),
    ]


def test_filter_requires_all_tags_in_any_spelling(editor, site, device):
    both = create_device(editor, name="both", kind="physical", site_id=site.id)
    create_device(editor, name="none", kind="physical", site_id=site.id)
    add_tag(editor, "device", device.id, "prod")
    add_tag(editor, "device", both.id, "prod")
    add_tag(editor, "device", both.id, "dmz")
    items = list_devices(editor)

    assert [d.name for d in filter_by_tags("device", items, ["PROD"])] == ["both", "server"]
    assert [d.name for d in filter_by_tags("device", items, ["prod", "DMZ"])] == ["both"]
    assert filter_by_tags("device", items, ["unknown"]) == []
    assert filter_by_tags("device", items, ["a\x00b"]) == []


def test_deleting_a_tag_previews_and_records_every_item(editor, site, device):
    tag = add_tag(editor, "device", device.id, "prod")
    add_tag(editor, "site", site.id, "prod")
    add_tag(editor, "device", device.id, "keep")

    preview = preview_delete(editor, "tag", tag.id)

    assert [(c.kind, c.label, c.effect) for c in preview.changed] == [
        ("device", "server", "loses this Tag"),
        ("site", "Home", "loses this Tag"),
    ]
    assert names(editor, "device", device.id) == ["keep", "prod"]

    confirm_delete(editor, "tag", tag.id, preview.fingerprint)

    assert names(editor, "device", device.id) == ["keep"]
    assert names(editor, "site", site.id) == []
    assert item_history(editor, "device", device.id)[0].action == "updated"
    assert item_history(editor, "tag", tag.id)[0].action == "deleted"


def test_deleting_an_item_keeps_the_tag(editor, device):
    add_tag(editor, "device", device.id, "prod")

    delete_device(editor, device.id)

    assert [(tag.name, tag.usage) for tag in list_tags(editor)] == [("prod", 0)]
    deleted = item_history(editor, "device", device.id)[0]
    assert "tags" in [c.field for c in deleted.changes]


def test_tag_writes_outside_the_services_are_refused(editor, device):
    tag = create_tag(editor, "prod")

    with pytest.raises(UntrackedWrite):
        Device.objects.get(pk=device.id).tags.add(Tag.objects.get(pk=tag.id))


def test_unknown_kind_item_or_tag_is_not_found(editor, device):
    tag = create_tag(editor, "prod")
    for action in (
        lambda: add_tag(editor, "widget", device.id, "prod"),
        lambda: add_tag(editor, "device", 999999, "prod"),
        lambda: tags_of(editor, "widget", device.id),
        lambda: tags_of(editor, "device", 999999),
        lambda: remove_tag(editor, "device", device.id, tag.id),
        lambda: remove_tag(editor, "device", device.id, 999999),
        lambda: get_tag(editor, 999999),
    ):
        with pytest.raises(Http404):
            action()


def test_viewer_reads_but_cannot_change_tags(admin, editor, device):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    tag = add_tag(editor, "device", device.id, "prod")

    assert names(viewer, "device", device.id) == ["prod"]
    assert [t.id for t in list_tags(viewer)] == [tag.id]
    assert len(tag_usage(viewer, tag.id).items) == 1
    for action in (
        lambda: create_tag(viewer, "dev"),
        lambda: update_tag(viewer, tag.id, name="x"),
        lambda: add_tag(viewer, "device", device.id, "dev"),
        lambda: remove_tag(viewer, "device", device.id, tag.id),
        lambda: preview_delete(viewer, "tag", tag.id),
    ):
        with pytest.raises(PermissionDenied):
            action()
