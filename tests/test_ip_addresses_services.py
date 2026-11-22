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
    delete_dhcp_range,
    delete_ip_address,
    delete_subnet,
    get_dhcp_range,
    get_ip_address,
    get_subnet,
    list_dhcp_ranges,
    list_ip_addresses,
    subnet_usage,
    unassign_ip_address,
    update_dhcp_range,
    update_ip_address,
    update_subnet,
)
from netatlas.devices.services import (
    create_device,
    create_interface,
    delete_device,
    delete_interface,
    get_device,
    get_interface,
    update_device,
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
def office(editor):
    return create_site(editor, name="Office")


@pytest.fixture
def subnet(editor, site):
    return create_subnet(editor, cidr="192.168.1.0/24", site_id=site.id)


@pytest.fixture
def device(editor, site):
    return create_device(editor, name="server", kind="physical", site_id=site.id)


@pytest.fixture
def port(editor, device):
    return create_interface(editor, device.id, name="eth0", kind="physical")


def test_editor_creates_edits_and_deletes_a_reserved_address(editor, site, subnet):
    created = create_ip_address(
        editor, address="192.168.1.10", site_id=site.id, description=" Printer "
    )

    assert (created.address, created.status, created.subnet_id, created.description) == (
        "192.168.1.10",
        "reserved",
        subnet.id,
        "Printer",
    )
    assert item_history(editor, "ip-address", created.id)[0].item_label == "192.168.1.10"

    assert update_ip_address(editor, created.id, address="192.168.1.11").address == "192.168.1.11"

    delete_ip_address(editor, created.id)
    with pytest.raises(Http404):
        get_ip_address(editor, created.id)
    assert item_history(editor, "ip-address", created.id)[0].action == "deleted"


def test_ipv6_is_stored_in_canonical_form(editor, site):
    create_subnet(editor, cidr="2001:db8::/64", site_id=site.id)

    created = create_ip_address(editor, address=" 2001:0DB8:0::A ", site_id=site.id)

    assert created.address == "2001:db8::a"
    with pytest.raises(ValidationError):
        create_ip_address(editor, address="2001:db8:0::a", site_id=site.id)


@pytest.mark.parametrize(
    "address",
    ["", "nonsense", "192.168.1.256", "192.168.1.10/24", "fe80::1%eth0", "192.168.1.1\x00"],
)
def test_invalid_address_is_a_field_error(editor, site, subnet, address):
    with pytest.raises(ValidationError) as error:
        create_ip_address(editor, address=address, site_id=site.id)

    assert "address" in error.value.message_dict


def test_address_must_be_inside_a_subnet_of_its_site(editor, site, office, subnet):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=office.id)

    for address in ("172.16.0.1", "10.0.0.1", "2001:db8::1"):
        with pytest.raises(ValidationError) as error:
            create_ip_address(editor, address=address, site_id=site.id)
        assert "address" in error.value.message_dict


def test_same_address_is_refused_in_a_site_but_allowed_in_another(editor, site, office, subnet):
    create_subnet(editor, cidr="192.168.1.0/24", site_id=office.id)
    create_ip_address(editor, address="192.168.1.10", site_id=site.id)

    with pytest.raises(ValidationError):
        create_ip_address(editor, address="192.168.1.10", site_id=site.id)

    assert create_ip_address(editor, address="192.168.1.10", site_id=office.id).id


def test_address_belongs_to_the_most_specific_subnet(editor, site):
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    middle = create_subnet(editor, cidr="10.1.0.0/16", site_id=site.id)
    create_subnet(editor, cidr="10.1.2.0/24", site_id=site.id)

    created = create_ip_address(editor, address="10.1.3.1", site_id=site.id)

    assert (created.subnet_id, created.subnet_cidr) == (middle.id, "10.1.0.0/16")


@pytest.mark.parametrize("address", ["192.168.1.0", "192.168.1.255"])
def test_network_and_broadcast_addresses_are_refused(editor, site, subnet, address):
    with pytest.raises(ValidationError) as error:
        create_ip_address(editor, address=address, site_id=site.id)

    assert "address" in error.value.message_dict


@pytest.mark.parametrize(
    ("cidr", "address"),
    [
        ("10.0.0.0/31", "10.0.0.0"),
        ("10.0.0.0/31", "10.0.0.1"),
        ("10.0.0.5/32", "10.0.0.5"),
        ("2001:db8::/64", "2001:db8::"),
    ],
)
def test_subnets_without_special_addresses_can_use_all(editor, site, cidr, address):
    create_subnet(editor, cidr=cidr, site_id=site.id)

    assert create_ip_address(editor, address=address, site_id=site.id).address == address


def test_address_is_assigned_and_unassigned(editor, site, subnet, port):
    created = create_ip_address(
        editor, address="192.168.1.10", site_id=site.id, interface_id=port.id
    )
    assert (created.status, created.interface_id, created.interface_name, created.device_name) == (
        "assigned",
        port.id,
        "eth0",
        "server",
    )

    released = unassign_ip_address(editor, created.id)
    assert (released.status, released.interface_id) == ("reserved", None)
    latest = item_history(editor, "ip-address", created.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [("interface", port.id, None)]


def test_assign_uses_an_existing_reserved_address_or_creates_one(editor, site, subnet, port):
    reserved = create_ip_address(editor, address="192.168.1.10", site_id=site.id)

    existing = assign_ip_address(editor, port.id, "192.168.1.10")
    created = assign_ip_address(editor, port.id, "192.168.1.11")

    assert (existing.id, existing.status) == (reserved.id, "assigned")
    assert (created.address, created.interface_id) == ("192.168.1.11", port.id)


def test_address_on_another_interface_cannot_be_taken(editor, device, subnet, port):
    other = create_interface(editor, device.id, name="eth1", kind="physical")
    assign_ip_address(editor, port.id, "192.168.1.10")

    with pytest.raises(ValidationError) as error:
        assign_ip_address(editor, other.id, "192.168.1.10")

    assert "address" in error.value.message_dict


def test_interface_must_be_in_the_addresses_site(editor, office, subnet, site):
    remote = create_device(editor, name="remote", kind="physical", site_id=office.id)
    remote_port = create_interface(editor, remote.id, name="eth0", kind="physical")

    with pytest.raises(ValidationError) as error:
        create_ip_address(
            editor, address="192.168.1.10", site_id=site.id, interface_id=remote_port.id
        )
    assert "interface_id" in error.value.message_dict

    created = create_ip_address(editor, address="192.168.1.10", site_id=site.id)
    with pytest.raises(ValidationError) as error:
        update_ip_address(editor, created.id, interface_id=remote_port.id)
    assert "interface_id" in error.value.message_dict


def test_interface_and_device_show_all_their_addresses(editor, device, subnet, port):
    other = create_interface(editor, device.id, name="eth1", kind="physical")
    assign_ip_address(editor, port.id, "192.168.1.20")
    assign_ip_address(editor, port.id, "192.168.1.3")
    assign_ip_address(editor, other.id, "192.168.1.5")

    assert [i.address for i in get_interface(editor, port.id).ip_addresses] == [
        "192.168.1.3",
        "192.168.1.20",
    ]
    assert [
        [i.address for i in interface.ip_addresses]
        for interface in get_device(editor, device.id).interfaces
    ] == [["192.168.1.3", "192.168.1.20"], ["192.168.1.5"]]


def test_deleting_an_interface_makes_its_addresses_reserved(editor, subnet, port):
    address = assign_ip_address(editor, port.id, "192.168.1.10")

    delete_interface(editor, port.id)

    stored = get_ip_address(editor, address.id)
    assert (stored.status, stored.interface_id) == ("reserved", None)
    latest = item_history(editor, "ip-address", address.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [("interface", port.id, None)]


def test_deleting_a_device_makes_its_addresses_reserved(editor, device, subnet, port):
    address = assign_ip_address(editor, port.id, "192.168.1.10")

    delete_device(editor, device.id)

    assert get_ip_address(editor, address.id).status == "reserved"


def test_device_with_assigned_addresses_cannot_change_site(editor, office, device, subnet, port):
    address = assign_ip_address(editor, port.id, "192.168.1.10")

    with pytest.raises(ValidationError) as error:
        update_device(editor, device.id, site_id=office.id)
    assert "site_id" in error.value.message_dict

    unassign_ip_address(editor, address.id)
    assert update_device(editor, device.id, site_id=office.id).site_id == office.id


def test_hypervisor_cannot_move_while_a_nested_device_has_addresses(
    editor, office, device, subnet
):
    vm = create_device(editor, name="vm", kind="virtual", parent_id=device.id)
    eth = create_interface(editor, vm.id, name="eth0", kind="virtual")
    assign_ip_address(editor, eth.id, "192.168.1.10")

    with pytest.raises(ValidationError) as error:
        update_device(editor, device.id, site_id=office.id)

    assert "site_id" in error.value.message_dict


def test_addresses_list_numerically_and_filter(editor, site, office, subnet, port):
    create_subnet(editor, cidr="192.168.1.0/24", site_id=office.id)
    create_ip_address(editor, address="192.168.1.100", site_id=site.id)
    assigned = assign_ip_address(editor, port.id, "192.168.1.9")
    elsewhere = create_ip_address(editor, address="192.168.1.50", site_id=office.id)

    assert [a.address for a in list_ip_addresses(editor, site_id=site.id)] == [
        "192.168.1.9",
        "192.168.1.100",
    ]
    assert [a.id for a in list_ip_addresses(editor, site_id=office.id)] == [elsewhere.id]
    assert [a.id for a in list_ip_addresses(editor, status="assigned")] == [assigned.id]
    assert [a.id for a in list_ip_addresses(editor, interface_id=port.id)] == [assigned.id]
    assert [a.address for a in list_ip_addresses(editor, subnet_id=subnet.id)] == [
        "192.168.1.9",
        "192.168.1.100",
    ]


def test_editor_creates_edits_and_deletes_a_dhcp_range(editor, subnet):
    created = create_dhcp_range(editor, subnet.id, " 192.168.1.100", "192.168.1.200 ")

    assert (created.start, created.end, created.subnet_id, created.size) == (
        "192.168.1.100",
        "192.168.1.200",
        subnet.id,
        "101",
    )
    assert [r.id for r in list_dhcp_ranges(editor, subnet_id=subnet.id)] == [created.id]
    assert item_history(editor, "dhcp-range", created.id)[0].action == "created"

    assert update_dhcp_range(editor, created.id, end="192.168.1.150").size == "51"

    delete_dhcp_range(editor, created.id)
    with pytest.raises(Http404):
        get_dhcp_range(editor, created.id)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("nonsense", "192.168.1.20"),
        ("192.168.1.30", "192.168.1.20"),
        ("192.168.0.1", "192.168.1.20"),
        ("192.168.1.10", "192.168.2.1"),
        ("192.168.1.0", "192.168.1.20"),
        ("192.168.1.10", "192.168.1.255"),
        ("192.168.1.1", "2001:db8::1"),
    ],
)
def test_invalid_range_is_refused(editor, subnet, start, end):
    with pytest.raises(ValidationError):
        create_dhcp_range(editor, subnet.id, start, end)

    assert list_dhcp_ranges(editor) == []


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("192.168.1.90", "192.168.1.100"),
        ("192.168.1.150", "192.168.1.250"),
        ("192.168.1.120", "192.168.1.130"),
        ("192.168.1.50", "192.168.1.250"),
    ],
)
def test_ranges_cannot_overlap(editor, subnet, start, end):
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    with pytest.raises(ValidationError):
        create_dhcp_range(editor, subnet.id, start, end)


def test_adjacent_ranges_are_allowed(editor, subnet):
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    assert create_dhcp_range(editor, subnet.id, "192.168.1.201", "192.168.1.210").id


def test_editing_a_range_ignores_itself_for_overlap(editor, subnet):
    created = create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    assert update_dhcp_range(editor, created.id, start="192.168.1.110").start == "192.168.1.110"


def test_range_cannot_overlap_a_nested_subnet(editor, site, subnet):
    create_subnet(editor, cidr="192.168.1.128/25", site_id=site.id)

    with pytest.raises(ValidationError):
        create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.130")


def test_nested_subnet_cannot_overlap_an_existing_range(editor, site, subnet):
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr="192.168.1.128/25", site_id=site.id)

    assert "cidr" in error.value.message_dict


def test_address_inside_a_range_is_refused(editor, site, subnet):
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    for address in ("192.168.1.100", "192.168.1.150", "192.168.1.200"):
        with pytest.raises(ValidationError) as error:
            create_ip_address(editor, address=address, site_id=site.id)
        assert "address" in error.value.message_dict

    assert create_ip_address(editor, address="192.168.1.99", site_id=site.id).id


def test_address_cannot_be_edited_into_a_range(editor, site, subnet):
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")
    created = create_ip_address(editor, address="192.168.1.10", site_id=site.id)

    with pytest.raises(ValidationError):
        update_ip_address(editor, created.id, address="192.168.1.150")

    assert get_ip_address(editor, created.id).address == "192.168.1.10"


def test_range_cannot_cover_existing_addresses(editor, site, subnet):
    create_ip_address(editor, address="192.168.1.150", site_id=site.id)

    with pytest.raises(ValidationError) as error:
        create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    assert "192.168.1.150" in " ".join(error.value.messages)


def test_deleting_a_subnet_moves_its_addresses_to_the_parent(editor, site):
    parent = create_subnet(editor, cidr="10.0.0.0/16", site_id=site.id)
    child = create_subnet(editor, cidr="10.0.1.0/24", site_id=site.id)
    address = create_ip_address(editor, address="10.0.1.5", site_id=site.id)
    assert get_ip_address(editor, address.id).subnet_id == child.id

    delete_subnet(editor, child.id)

    assert get_ip_address(editor, address.id).subnet_id == parent.id


def test_subnet_cannot_be_deleted_if_addresses_would_be_orphaned(editor, site, subnet):
    address = create_ip_address(editor, address="192.168.1.10", site_id=site.id)

    with pytest.raises(ValidationError) as error:
        delete_subnet(editor, subnet.id)

    assert "1" in " ".join(error.value.messages)
    assert get_subnet(editor, subnet.id).id == subnet.id
    assert get_ip_address(editor, address.id).subnet_id == subnet.id


def test_subnet_with_a_range_cannot_be_deleted(editor, subnet):
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    with pytest.raises(ValidationError):
        delete_subnet(editor, subnet.id)

    assert get_subnet(editor, subnet.id).id == subnet.id


def test_subnet_cidr_change_cannot_orphan_addresses_or_ranges(editor, site):
    with_address = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    create_ip_address(editor, address="10.0.0.200", site_id=site.id)
    with_range = create_subnet(editor, cidr="10.0.1.0/24", site_id=site.id)
    create_dhcp_range(editor, with_range.id, "10.0.1.200", "10.0.1.210")

    for subnet_id, cidr in ((with_address.id, "10.0.0.0/25"), (with_range.id, "10.0.1.0/25")):
        with pytest.raises(ValidationError) as error:
            update_subnet(editor, subnet_id, cidr=cidr)
        assert "cidr" in error.value.message_dict

    assert get_subnet(editor, with_address.id).cidr == "10.0.0.0/24"


def test_subnet_can_grow_around_its_addresses(editor, site):
    created = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    address = create_ip_address(editor, address="10.0.0.200", site_id=site.id)

    update_subnet(editor, created.id, cidr="10.0.0.0/23")

    assert get_ip_address(editor, address.id).subnet_cidr == "10.0.0.0/23"


@pytest.mark.parametrize("address", ["192.168.1.127", "192.168.1.128"])
def test_new_nested_subnet_cannot_turn_an_address_into_a_special_one(
    editor, site, subnet, address
):
    create_ip_address(editor, address=address, site_id=site.id)
    cidr = "192.168.1.0/25" if address.endswith("127") else "192.168.1.128/25"

    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr=cidr, site_id=site.id)

    assert "cidr" in error.value.message_dict


@pytest.mark.parametrize("use", ["address", "range"])
def test_subnet_in_use_cannot_change_site(editor, site, office, subnet, use):
    if use == "address":
        create_ip_address(editor, address="192.168.1.10", site_id=site.id)
    else:
        create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    with pytest.raises(ValidationError) as error:
        update_subnet(editor, subnet.id, site_id=office.id)

    assert "site_id" in error.value.message_dict


def test_site_with_addresses_cannot_be_deleted(editor, site, office):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=office.id)
    address = create_ip_address(editor, address="10.0.0.1", site_id=office.id)

    with pytest.raises(ValidationError):
        delete_site(editor, office.id)

    assert get_ip_address(editor, address.id).site_id == office.id


def test_usage_counts_each_kind_of_space(editor, site, subnet, port):
    create_subnet(editor, cidr="192.168.1.192/26", site_id=site.id)
    assign_ip_address(editor, port.id, "192.168.1.1")
    create_ip_address(editor, address="192.168.1.2", site_id=site.id)
    create_ip_address(editor, address="192.168.1.3", site_id=site.id)
    create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.109")

    usage = subnet_usage(editor, subnet.id)

    assert (usage.assigned, usage.reserved, usage.dhcp, usage.nested, usage.free) == (
        1,
        2,
        "10",
        "64",
        "178",
    )
    assert usage.next_free == "192.168.1.4"
    assert [a.address for a in usage.ip_addresses] == [
        "192.168.1.1",
        "192.168.1.2",
        "192.168.1.3",
    ]
    assert [r.start for r in usage.dhcp_ranges] == ["192.168.1.100"]


def test_addresses_in_a_nested_subnet_are_not_counted_in_the_parent(editor, site, subnet):
    create_subnet(editor, cidr="192.168.1.128/25", site_id=site.id)
    create_ip_address(editor, address="192.168.1.130", site_id=site.id)

    usage = subnet_usage(editor, subnet.id)

    assert (usage.reserved, usage.ip_addresses) == (0, [])


def test_next_free_skips_used_space(editor, site, subnet):
    create_ip_address(editor, address="192.168.1.1", site_id=site.id)
    create_dhcp_range(editor, subnet.id, "192.168.1.2", "192.168.1.100")
    create_subnet(editor, cidr="192.168.1.104/29", site_id=site.id)
    create_ip_address(editor, address="192.168.1.101", site_id=site.id)

    assert subnet_usage(editor, subnet.id).next_free == "192.168.1.102"


def test_a_full_subnet_has_no_next_free_and_shows_100_percent(editor, site):
    full = create_subnet(editor, cidr="10.0.0.0/30", site_id=site.id)
    create_ip_address(editor, address="10.0.0.1", site_id=site.id)
    create_ip_address(editor, address="10.0.0.2", site_id=site.id)

    usage = subnet_usage(editor, full.id)

    assert (usage.free, usage.next_free) == ("0", None)
    assert get_subnet(editor, full.id).utilisation == 100.0


@pytest.mark.parametrize(
    ("cidr", "expected"),
    [("10.0.0.0/31", "10.0.0.0"), ("10.0.0.5/32", "10.0.0.5"), ("2001:db8::/64", "2001:db8::")],
)
def test_next_free_uses_all_addresses_where_none_are_special(editor, site, cidr, expected):
    created = create_subnet(editor, cidr=cidr, site_id=site.id)

    assert subnet_usage(editor, created.id).next_free == expected


def test_usage_works_for_huge_ipv6_subnets(editor, site):
    created = create_subnet(editor, cidr="2001:db8::/32", site_id=site.id)
    create_ip_address(editor, address="2001:db8::", site_id=site.id)

    usage = subnet_usage(editor, created.id)

    assert usage.free == str(2**96 - 1)
    assert usage.next_free == "2001:db8::1"


def test_utilisation_counts_usable_addresses(editor, site):
    created = create_subnet(editor, cidr="10.0.0.0/30", site_id=site.id)
    create_ip_address(editor, address="10.0.0.1", site_id=site.id)

    assert get_subnet(editor, created.id).utilisation == 50.0


def test_utilisation_includes_dhcp_ranges(editor, site):
    created = create_subnet(editor, cidr="10.0.0.0/29", site_id=site.id)
    create_dhcp_range(editor, created.id, "10.0.0.1", "10.0.0.3")

    assert get_subnet(editor, created.id).utilisation == 50.0


def test_viewer_reads_but_cannot_change_addresses(admin, editor, site, subnet, port):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    address = create_ip_address(editor, address="192.168.1.10", site_id=site.id)
    dhcp = create_dhcp_range(editor, subnet.id, "192.168.1.100", "192.168.1.200")

    assert get_ip_address(viewer, address.id).address == "192.168.1.10"
    assert [a.id for a in list_ip_addresses(viewer)] == [address.id]
    assert get_dhcp_range(viewer, dhcp.id).start == "192.168.1.100"
    assert subnet_usage(viewer, subnet.id).reserved == 1
    for action in (
        lambda: create_ip_address(viewer, address="192.168.1.11", site_id=site.id),
        lambda: update_ip_address(viewer, address.id, description="x"),
        lambda: delete_ip_address(viewer, address.id),
        lambda: assign_ip_address(viewer, port.id, "192.168.1.11"),
        lambda: unassign_ip_address(viewer, address.id),
        lambda: create_dhcp_range(viewer, subnet.id, "192.168.1.1", "192.168.1.2"),
        lambda: update_dhcp_range(viewer, dhcp.id, end="192.168.1.201"),
        lambda: delete_dhcp_range(viewer, dhcp.id),
    ):
        with pytest.raises(PermissionDenied):
            action()
