import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.addressing.services import (
    create_subnet,
    delete_subnet,
    get_subnet,
    list_subnets,
    update_subnet,
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


def tree(actor, **filters):
    return [(s.depth, s.cidr) for s in list_subnets(actor, **filters)]


def test_editor_creates_edits_and_deletes_a_subnet(editor, site):
    created = create_subnet(editor, cidr="192.168.1.0/24", site_id=site.id)

    assert (created.cidr, created.version, created.prefix_length, created.site_name) == (
        "192.168.1.0/24",
        4,
        24,
        "Home",
    )
    assert item_history(editor, "subnet", created.id)[0].item_label == "192.168.1.0/24"

    assert update_subnet(editor, created.id, cidr="192.168.2.0/24").cidr == "192.168.2.0/24"
    latest = item_history(editor, "subnet", created.id)[0]
    assert [(c.field, c.old, c.new) for c in latest.changes] == [
        ("cidr", "192.168.1.0/24", "192.168.2.0/24")
    ]

    delete_subnet(editor, created.id)
    with pytest.raises(Http404):
        get_subnet(editor, created.id)
    assert item_history(editor, "subnet", created.id)[0].action == "deleted"


@pytest.mark.parametrize(
    ("entered", "stored", "version"),
    [
        ("2001:db8::/32", "2001:db8::/32", 6),
        ("2001:DB8:0:0::/64", "2001:db8::/64", 6),
        ("  10.0.0.0/8  ", "10.0.0.0/8", 4),
        ("10.0.0.1/32", "10.0.0.1/32", 4),
        ("0.0.0.0/0", "0.0.0.0/0", 4),
        ("::/0", "::/0", 6),
    ],
)
def test_cidr_is_stored_in_canonical_form(editor, site, entered, stored, version):
    created = create_subnet(editor, cidr=entered, site_id=site.id)

    assert (created.cidr, created.version) == (stored, version)


@pytest.mark.parametrize(
    "cidr",
    [
        "",
        "nonsense",
        "10.0.0.0",
        "2001:db8::",
        "10.0.0.0/33",
        "2001:db8::/129",
        "256.0.0.0/8",
        "10.0.0.0/255.0.0.0",
        "10.0.0.0/0.255.255.255",
        "10.0.0.0/-1",
        "10.0.0.0/8\x00",
    ],
)
def test_invalid_cidr_is_a_field_error(editor, site, cidr):
    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr=cidr, site_id=site.id)

    assert "cidr" in error.value.message_dict
    assert list_subnets(editor) == []


@pytest.mark.parametrize(
    ("cidr", "suggestion"),
    [("192.168.1.5/24", "192.168.1.0/24"), ("2001:db8::1/64", "2001:db8::/64")],
)
def test_host_bits_are_refused_with_the_correct_form(editor, site, cidr, suggestion):
    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr=cidr, site_id=site.id)

    assert suggestion in error.value.message_dict["cidr"][0]


def test_host_bits_are_refused_on_edit(editor, site):
    subnet = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)

    with pytest.raises(ValidationError) as error:
        update_subnet(editor, subnet.id, cidr="10.0.0.1/24")

    assert "cidr" in error.value.message_dict
    assert get_subnet(editor, subnet.id).cidr == "10.0.0.0/24"


def test_duplicate_is_refused_within_a_site_but_allowed_in_another(editor, site):
    other = create_site(editor, name="Office")
    create_subnet(editor, cidr="192.168.1.0/24", site_id=site.id)

    with pytest.raises(ValidationError):
        create_subnet(editor, cidr="192.168.1.0/24", site_id=site.id)

    assert create_subnet(editor, cidr="192.168.1.0/24", site_id=other.id).site_id == other.id


def test_two_spellings_of_one_ipv6_subnet_are_duplicates(editor, site):
    create_subnet(editor, cidr="2001:db8::/32", site_id=site.id)

    with pytest.raises(ValidationError):
        create_subnet(editor, cidr="2001:0DB8:0000::/32", site_id=site.id)


def test_editing_into_a_duplicate_is_refused(editor, site):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    other = create_subnet(editor, cidr="10.0.1.0/24", site_id=site.id)

    with pytest.raises(ValidationError):
        update_subnet(editor, other.id, cidr="10.0.0.0/24")


def test_unknown_site_is_a_field_error(editor):
    with pytest.raises(ValidationError) as error:
        create_subnet(editor, cidr="10.0.0.0/24", site_id=999999)

    assert "site" in error.value.message_dict


def test_subnets_form_a_tree_in_numeric_order(editor, site):
    for cidr in (
        "10.1.10.128/25",
        "10.2.0.0/16",
        "10.1.9.0/24",
        "10.1.0.0/16",
        "10.1.10.0/24",
        "9.0.0.0/8",
        "2001:db8:1::/48",
        "2001:db8::/32",
    ):
        create_subnet(editor, cidr=cidr, site_id=site.id)

    assert tree(editor) == [
        (0, "9.0.0.0/8"),
        (0, "10.1.0.0/16"),
        (1, "10.1.9.0/24"),
        (1, "10.1.10.0/24"),
        (2, "10.1.10.128/25"),
        (0, "10.2.0.0/16"),
        (0, "2001:db8::/32"),
        (1, "2001:db8:1::/48"),
    ]


def test_parent_is_the_most_specific_containing_subnet(editor, site):
    wide = create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    middle = create_subnet(editor, cidr="10.1.0.0/16", site_id=site.id)
    narrow = create_subnet(editor, cidr="10.1.2.0/24", site_id=site.id)

    stored = get_subnet(editor, narrow.id)

    assert (stored.parent_id, stored.parent_cidr, stored.depth) == (middle.id, "10.1.0.0/16", 2)
    assert get_subnet(editor, wide.id).parent_id is None


def test_nesting_never_crosses_sites(editor, site):
    other = create_site(editor, name="Office")
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    inner = create_subnet(editor, cidr="10.1.0.0/16", site_id=other.id)

    assert get_subnet(editor, inner.id).parent_id is None


def test_deleting_a_parent_moves_its_children_up(editor, site):
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    middle = create_subnet(editor, cidr="10.1.0.0/16", site_id=site.id)
    create_subnet(editor, cidr="10.1.2.0/24", site_id=site.id)

    delete_subnet(editor, middle.id)

    assert tree(editor) == [(0, "10.0.0.0/8"), (1, "10.1.2.0/24")]


def test_editing_a_cidr_rebuilds_the_tree(editor, site):
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    moved = create_subnet(editor, cidr="10.1.0.0/16", site_id=site.id)

    update_subnet(editor, moved.id, cidr="172.16.0.0/16")

    assert tree(editor) == [(0, "10.0.0.0/8"), (0, "172.16.0.0/16")]


def test_a_subnet_can_move_to_another_site(editor, site):
    other = create_site(editor, name="Office")
    subnet = create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)

    assert update_subnet(editor, subnet.id, site_id=other.id).site_name == "Office"


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        ([], 0.0),
        (["10.0.0.0/25"], 50.0),
        (["10.0.0.0/25", "10.0.0.128/25"], 100.0),
        (["10.0.0.0/26"], 25.0),
    ],
)
def test_utilisation_is_the_share_covered_by_children(editor, site, children, expected):
    parent = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    for cidr in children:
        create_subnet(editor, cidr=cidr, site_id=site.id)

    assert get_subnet(editor, parent.id).utilisation == expected


def test_utilisation_counts_nested_space_only_once(editor, site):
    parent = create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/25", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/26", site_id=site.id)

    assert get_subnet(editor, parent.id).utilisation == 50.0


def test_utilisation_never_rounds_up_to_full(editor, site):
    parent = create_subnet(editor, cidr="10.0.0.0/16", site_id=site.id)
    for prefix, network in (
        (17, "10.0.0.0"),
        (18, "10.0.128.0"),
        (19, "10.0.192.0"),
        (20, "10.0.224.0"),
        (21, "10.0.240.0"),
        (22, "10.0.248.0"),
        (23, "10.0.252.0"),
        (24, "10.0.254.0"),
        (25, "10.0.255.0"),
        (26, "10.0.255.128"),
        (27, "10.0.255.192"),
    ):
        create_subnet(editor, cidr=f"{network}/{prefix}", site_id=site.id)

    assert get_subnet(editor, parent.id).utilisation == 99.9


def test_utilisation_works_for_huge_ipv6_subnets(editor, site):
    parent = create_subnet(editor, cidr="::/0", site_id=site.id)
    create_subnet(editor, cidr="8000::/1", site_id=site.id)

    assert get_subnet(editor, parent.id).utilisation == 50.0


def test_subnets_filter_by_site_and_version(editor, site):
    other = create_site(editor, name="Office")
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    create_subnet(editor, cidr="2001:db8::/32", site_id=site.id)
    create_subnet(editor, cidr="172.16.0.0/12", site_id=other.id)

    assert tree(editor, site_id=other.id) == [(0, "172.16.0.0/12")]
    assert tree(editor, version=6) == [(0, "2001:db8::/32")]


def test_site_with_a_subnet_cannot_be_deleted(editor, site):
    create_site(editor, name="Office")
    subnet = create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)

    with pytest.raises(ValidationError):
        delete_site(editor, site.id)

    assert get_subnet(editor, subnet.id).site_id == site.id


def test_viewer_reads_but_cannot_change_subnets(admin, editor, site):
    viewer = create_user(admin, "viewer", "yet-another-long-password", Role.VIEWER)
    subnet = create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)

    assert get_subnet(viewer, subnet.id).cidr == "10.0.0.0/8"
    assert [s.id for s in list_subnets(viewer)] == [subnet.id]
    for action in (
        lambda: create_subnet(viewer, cidr="172.16.0.0/12", site_id=site.id),
        lambda: update_subnet(viewer, subnet.id, cidr="172.16.0.0/12"),
        lambda: delete_subnet(viewer, subnet.id),
    ):
        with pytest.raises(PermissionDenied):
            action()
