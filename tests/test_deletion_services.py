import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_ip_address,
    create_subnet,
    create_vlan,
    get_ip_address,
    get_subnet,
    list_subnets,
)
from netatlas.deletion.services import (
    DELETABLE_KINDS,
    StaleDelete,
    confirm_delete,
    preview_delete,
)
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
    get_device,
    get_interface,
    list_devices,
    pair_interfaces,
    update_device,
)
from netatlas.history.services import item_history
from netatlas.places.services import create_customer, create_site, get_site, list_sites

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


@pytest.fixture
def port(editor, device):
    return create_interface(editor, device.id, name="eth0", kind="physical")


def labels(impacts):
    return sorted((impact.kind, impact.label) for impact in impacts)


def test_every_kind_of_documented_item_can_be_previewed():
    assert set(DELETABLE_KINDS) == {
        "customer",
        "site",
        "device",
        "interface",
        "rack",
        "vlan",
        "subnet",
        "dhcp-range",
        "ip-address",
        "cable",
        "device-type",
        "tag",
        "page",
    }


def test_device_preview_lists_everything_it_affects_and_changes_nothing(
    editor, site, device, port
):
    other = create_device(editor, name="switch", kind="physical", site_id=site.id)
    far = create_interface(editor, other.id, name="p1", kind="physical")
    create_cable(editor, port.id, far.id, label="A-1")
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    address = assign_ip_address(editor, port.id, "10.0.0.5")
    history_before = len(item_history(editor, "device", device.id))

    preview = preview_delete(editor, "device", device.id)

    assert (preview.kind, preview.item_id, preview.item_label) == ("device", device.id, "server")
    assert preview.blocked == []
    assert labels(preview.deleted) == [
        ("cable", "A-1: server eth0 – switch p1"),
        ("interface", "server eth0"),
        ("page", "server"),
    ]
    assert [(c.kind, c.label, c.effect) for c in preview.changed] == [
        ("ip-address", "10.0.0.5", "becomes Reserved")
    ]
    assert get_device(editor, device.id).name == "server"
    assert get_interface(editor, port.id).cable_id is not None
    assert get_ip_address(editor, address.id).status == "assigned"
    assert len(item_history(editor, "device", device.id)) == history_before


def test_confirmed_delete_does_what_the_preview_said(editor, site, device, port):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    address = assign_ip_address(editor, port.id, "10.0.0.5")
    preview = preview_delete(editor, "device", device.id)

    confirm_delete(editor, "device", device.id, preview.fingerprint)

    with pytest.raises(Http404):
        get_device(editor, device.id)
    assert get_ip_address(editor, address.id).status == "reserved"
    assert item_history(editor, "device", device.id)[0].action == "deleted"
    assert item_history(editor, "interface", port.id)[0].action == "deleted"
    assert item_history(editor, "ip-address", address.id)[0].action == "updated"


def test_changed_impact_makes_the_confirmation_stale(editor, device, port):
    preview = preview_delete(editor, "device", device.id)
    added = create_interface(editor, device.id, name="eth1", kind="physical")

    with pytest.raises(StaleDelete):
        confirm_delete(editor, "device", device.id, preview.fingerprint)

    assert get_device(editor, device.id).name == "server"
    assert get_interface(editor, added.id).name == "eth1"
    assert item_history(editor, "device", device.id)[0].action == "created"


def test_renaming_the_item_makes_the_confirmation_stale(editor, device):
    preview = preview_delete(editor, "device", device.id)
    update_device(editor, device.id, name="renamed")

    with pytest.raises(StaleDelete):
        confirm_delete(editor, "device", device.id, preview.fingerprint)


@pytest.mark.parametrize("fingerprint", ["", "nonsense", "überall"])
def test_a_wrong_fingerprint_deletes_nothing(editor, device, fingerprint):
    with pytest.raises(StaleDelete):
        confirm_delete(editor, "device", device.id, fingerprint)

    assert get_device(editor, device.id).name == "server"


def test_same_state_gives_the_same_fingerprint(editor, device, port):
    first = preview_delete(editor, "device", device.id)
    second = preview_delete(editor, "device", device.id)

    assert first.fingerprint == second.fingerprint


def test_hypervisor_preview_is_blocked_and_names_its_virtual_devices(editor, device):
    create_device(editor, name="vm-1", kind="virtual", parent_id=device.id)

    preview = preview_delete(editor, "device", device.id)

    assert "vm-1" in " ".join(preview.blocked)
    assert (preview.deleted, preview.changed) == ([], [])
    with pytest.raises(ValidationError):
        confirm_delete(editor, "device", device.id, preview.fingerprint)
    assert [d.name for d in list_devices(editor) if d.id == device.id] == ["server"]


def test_last_site_preview_is_blocked(editor, site):
    assert preview_delete(editor, "site", site.id).blocked != []


def test_customer_preview_shows_sites_losing_their_customer(editor):
    customer = create_customer(editor, name="Acme")
    create_site(editor, name="Zurich", customer_id=customer.id)

    preview = preview_delete(editor, "customer", customer.id)

    assert [(c.kind, c.label, c.effect) for c in preview.changed] == [
        ("site", "Zurich", "loses its Customer")
    ]


def test_lag_preview_shows_members_leaving(editor, device):
    lag = create_interface(editor, device.id, name="lag0", kind="lag")
    create_interface(editor, device.id, name="p1", kind="physical", lag_id=lag.id)

    preview = preview_delete(editor, "interface", lag.id)

    assert [(c.label, c.effect) for c in preview.changed] == [("server p1", "leaves the LAG")]


def test_paired_interface_preview_shows_the_other_side(editor, device, port):
    rear = create_interface(editor, device.id, name="rear", kind="physical")
    pair_interfaces(editor, port.id, rear.id)

    preview = preview_delete(editor, "interface", port.id)

    assert [(c.label, c.effect) for c in preview.changed] == [("server rear", "loses its pair")]


def test_device_type_preview_shows_devices_keeping_their_copy(editor, site):
    template = create_device_type(editor, make="Acme", model_name="SW")
    create_device(editor, name="sw", kind="physical", site_id=site.id, device_type_id=template.id)

    preview = preview_delete(editor, "device-type", template.id)

    assert [(c.label, c.effect) for c in preview.changed] == [
        ("sw", "keeps its copy, without the Device Type")
    ]


def test_subnet_preview_shows_what_moves_up(editor, site):
    create_subnet(editor, cidr="10.0.0.0/16", site_id=site.id)
    middle = create_subnet(editor, cidr="10.0.1.0/24", site_id=site.id)
    create_subnet(editor, cidr="10.0.1.0/25", site_id=site.id)
    create_ip_address(editor, address="10.0.1.200", site_id=site.id)

    preview = preview_delete(editor, "subnet", middle.id)

    assert [(m.kind, m.label, m.effect) for m in preview.moved] == [
        ("subnet", "10.0.1.0/25", "moves to 10.0.0.0/16"),
        ("ip-address", "10.0.1.200", "moves to 10.0.0.0/16"),
    ]


def test_new_nested_item_makes_a_subnet_confirmation_stale(editor, site):
    create_subnet(editor, cidr="10.0.0.0/16", site_id=site.id)
    middle = create_subnet(editor, cidr="10.0.1.0/24", site_id=site.id)
    preview = preview_delete(editor, "subnet", middle.id)
    create_ip_address(editor, address="10.0.1.200", site_id=site.id)

    with pytest.raises(StaleDelete):
        confirm_delete(editor, "subnet", middle.id, preview.fingerprint)

    assert get_subnet(editor, middle.id).cidr == "10.0.1.0/24"


def test_subnet_preview_is_blocked_when_addresses_would_be_orphaned(editor, site):
    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    create_ip_address(editor, address="10.0.0.5", site_id=site.id)

    preview = preview_delete(editor, "subnet", subnet.id)

    assert preview.blocked != []
    assert [s.id for s in list_subnets(editor)] == [subnet.id]


@pytest.mark.parametrize("kind", ["vlan", "rack", "cable", "dhcp-range", "ip-address"])
def test_simple_kinds_preview_and_delete(editor, site, device, port, kind):
    if kind == "vlan":
        item = create_vlan(editor, number=10, site_id=site.id)
    elif kind == "rack":
        item = create_rack(editor, name="R1", site_id=site.id, height=4)
    elif kind == "cable":
        far = create_interface(editor, device.id, name="eth1", kind="physical")
        item = create_cable(editor, port.id, far.id)
    elif kind == "dhcp-range":
        subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
        item = create_dhcp_range(editor, subnet.id, "10.0.0.10", "10.0.0.20")
    else:
        create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
        item = create_ip_address(editor, address="10.0.0.5", site_id=site.id)

    preview = preview_delete(editor, kind, item.id)
    assert (preview.blocked, preview.changed) == ([], [])
    assert [i.kind for i in preview.deleted] == (["page"] if kind in ("vlan", "rack") else [])

    confirm_delete(editor, kind, item.id, preview.fingerprint)

    assert item_history(editor, kind, item.id)[0].action == "deleted"


def test_site_preview_and_delete(editor, site):
    other = create_site(editor, name="Office")

    preview = preview_delete(editor, "site", other.id)
    confirm_delete(editor, "site", other.id, preview.fingerprint)

    assert [s.id for s in list_sites(editor)] == [site.id]
    assert get_site(editor, site.id).name == "Home"


def test_unknown_kind_or_item_is_not_found(editor, device):
    with pytest.raises(Http404):
        preview_delete(editor, "widget", device.id)
    with pytest.raises(Http404):
        preview_delete(editor, "device", 999999)
    with pytest.raises(Http404):
        confirm_delete(editor, "widget", device.id, "x")
    with pytest.raises(Http404):
        confirm_delete(editor, "device", 999999, "x")


def test_viewer_cannot_preview_or_confirm(admin, editor, device):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    preview = preview_delete(editor, "device", device.id)

    with pytest.raises(PermissionDenied):
        preview_delete(viewer, "device", device.id)
    with pytest.raises(PermissionDenied):
        confirm_delete(viewer, "device", device.id, preview.fingerprint)

    assert get_device(editor, device.id).name == "server"
