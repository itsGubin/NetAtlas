import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin
from netatlas.addressing.services import (
    assign_ip_address,
    create_subnet,
    create_vlan,
    reserve_ip_address,
)
from netatlas.devices.services import create_device, create_interface, create_rack
from netatlas.pages.services import create_page, get_item_page, update_page
from netatlas.places.services import create_customer, create_site
from netatlas.search.services import MAX_HITS_PER_KIND, MAX_QUERY_LENGTH, search
from netatlas.tagging.services import add_tag


@pytest.fixture
def editor(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def site(editor):
    return create_site(editor, name="Basel")


@pytest.fixture
def other_site(editor):
    return create_site(editor, name="Zurich")


@pytest.fixture
def device(editor, site):
    return create_device(editor, name="core-sw", kind="physical", site_id=site.id)


@pytest.fixture
def port(editor, device):
    return create_interface(editor, device.id, name="ge-0/0/1", kind="physical")


def labels(results, kind):
    for group in results.groups:
        if group.kind == kind:
            return [hit.label for hit in group.hits]
    return []


def test_blank_search_finds_nothing(editor, device):
    assert search(editor, "   ").groups == []


def test_finds_every_kind(editor, site, device, port):
    customer = create_customer(editor, name="Findme Customer")
    create_site(editor, name="Findme Site", customer_id=customer.id)
    create_device(editor, name="findme-device", kind="physical", site_id=site.id)
    create_interface(editor, device.id, name="findme-if", kind="physical")
    create_rack(editor, name="findme-rack", site_id=site.id)
    create_vlan(editor, site_id=site.id, number=10, name="findme-vlan")
    create_page(editor, "Findme Page")
    subnet = create_subnet(editor, cidr="10.9.0.0/24", site_id=site.id)
    reserve_ip_address(editor, subnet.id, "10.9.0.7", description="findme address")

    found = {group.kind for group in search(editor, "findme").groups}
    assert found == {
        "customer",
        "site",
        "device",
        "interface",
        "rack",
        "vlan",
        "page",
        "ip-address",
    }


def test_names_match_inside_and_across_punctuation(editor, port):
    assert labels(search(editor, "sw"), "device") == ["core-sw"]
    assert labels(search(editor, "0/0/1"), "interface") == ["core-sw ge-0/0/1"]
    assert labels(search(editor, "core ge-0"), "interface") == ["core-sw ge-0/0/1"]


def test_every_word_must_match(editor, device):
    assert labels(search(editor, "core nothing"), "device") == []


def test_exact_name_comes_first(editor, site):
    create_device(editor, name="a-core", kind="physical", site_id=site.id)
    create_device(editor, name="core", kind="physical", site_id=site.id)
    assert labels(search(editor, "core"), "device") == ["core", "a-core"]


def test_vlan_by_number(editor, site):
    create_vlan(editor, site_id=site.id, number=10, name="mgmt")
    create_vlan(editor, site_id=site.id, number=110, name="guest")
    assert labels(search(editor, "10"), "vlan") == ["VLAN 10 mgmt"]
    assert labels(search(editor, "vlan 110"), "vlan") == ["VLAN 110 guest"]


def test_ip_finds_address_and_containing_subnets_most_specific_first(editor, site, port):
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    create_subnet(editor, cidr="10.1.0.0/24", site_id=site.id)
    assign_ip_address(editor, port.id, "10.0.0.5")
    assign_ip_address(editor, port.id, "10.0.0.50")

    results = search(editor, "10.0.0.5")
    assert labels(results, "ip-address") == ["10.0.0.5"]
    assert labels(results, "subnet") == ["10.0.0.0/24", "10.0.0.0/8"]
    hit = next(g for g in results.groups if g.kind == "ip-address").hits[0]
    assert hit.detail == "Basel · core-sw ge-0/0/1"


def test_cidr_finds_overlapping_subnets_and_addresses_inside(editor, site, port):
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/25", site_id=site.id)
    create_subnet(editor, cidr="10.1.0.0/24", site_id=site.id)
    assign_ip_address(editor, port.id, "10.0.0.5")
    assign_ip_address(editor, port.id, "10.1.0.5")

    results = search(editor, "10.0.0.0/24")
    assert labels(results, "subnet") == ["10.0.0.0/24", "10.0.0.0/25", "10.0.0.0/8"]
    assert labels(results, "ip-address") == ["10.0.0.5"]


def test_ipv6_is_compared_as_an_address(editor, site, port):
    create_subnet(editor, cidr="2001:db8::/64", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/8", site_id=site.id)
    assign_ip_address(editor, port.id, "2001:db8::5")

    results = search(editor, "2001:0db8:0::5")
    assert labels(results, "ip-address") == ["2001:db8::5"]
    assert labels(results, "subnet") == ["2001:db8::/64"]
    assert labels(search(editor, "2001:db8::/32"), "subnet") == ["2001:db8::/64"]


def test_page_text_is_searched_and_shows_an_excerpt(editor, device):
    page = get_item_page(editor, "device", device.id)
    update_page(editor, page.id, body="Before. The firmware upgrade needs a reboot. After.")

    group = next(g for g in search(editor, "firmw").groups if g.kind == "page")
    assert group.hits[0].id == page.id
    assert group.hits[0].label == "core-sw"
    assert "firmware upgrade" in group.hits[0].excerpt


def test_page_title_outranks_body(editor):
    create_page(editor, "Other", "backup is mentioned here")
    create_page(editor, "Backup plan", "text")
    assert labels(search(editor, "backup"), "page") == ["Backup plan", "Other"]


def test_stored_references_are_not_page_text(editor, device):
    create_page(
        editor,
        "Refs",
        f"@[device:{device.id}] @{{device:{device.id}.name}} ![x](attachment:{device.id})",
    )
    assert labels(search(editor, "device"), "page") == []
    assert labels(search(editor, "attachment"), "page") == []


def test_page_ip_search_matches_whole_addresses(editor):
    create_page(editor, "Exact", "gateway is 10.0.0.5.")
    create_page(editor, "Longer", "gateway is 10.0.0.50")
    create_page(editor, "Cidr", "see 10.0.0.5/24")
    assert labels(search(editor, "10.0.0.5"), "page") == ["Exact"]


@pytest.mark.parametrize(
    "query",
    ["'", "\\", "a & b", "!x", "foo:*", "(", "a' | 'b", "<->", "%", "_", "\x00core", "///", "a\\'"],
)
def test_query_syntax_is_treated_as_text(editor, device, query):
    create_page(editor, "Harmless", "some text")
    search(editor, query)


def test_like_wildcards_match_literally(editor, device):
    assert labels(search(editor, "%"), "device") == []
    assert labels(search(editor, "_"), "device") == []


def test_tag_filter_requires_every_tag(editor, site):
    one = create_device(editor, name="one", kind="physical", site_id=site.id)
    both = create_device(editor, name="both", kind="physical", site_id=site.id)
    add_tag(editor, "device", one.id, "prod")
    add_tag(editor, "device", both.id, "Prod")
    add_tag(editor, "device", both.id, "edge")

    assert sorted(labels(search(editor, "o", ["PROD"]), "device")) == ["both", "one"]
    assert labels(search(editor, "o", ["prod", "edge"]), "device") == ["both"]
    assert labels(search(editor, "", ["edge"]), "device") == ["both"]
    assert search(editor, "o", ["unknown"]).groups == []


def test_tag_filter_reaches_pages(editor):
    tagged = create_page(editor, "Tagged note", "x")
    create_page(editor, "Plain note", "x")
    add_tag(editor, "page", tagged.id, "prod")
    assert labels(search(editor, "note", ["prod"]), "page") == ["Tagged note"]


def test_site_filter(editor, site, other_site, port):
    other = create_device(editor, name="core-b", kind="physical", site_id=other_site.id)
    create_interface(editor, other.id, name="ge-0/0/1", kind="physical")
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    create_subnet(editor, cidr="10.0.0.0/24", site_id=other_site.id)

    assert labels(search(editor, "core", site_id=other_site.id), "device") == ["core-b"]
    assert labels(search(editor, "ge-0", site_id=other_site.id), "interface") == ["core-b ge-0/0/1"]
    in_site = next(
        g for g in search(editor, "10.0.0.1", site_id=site.id).groups if g.kind == "subnet"
    )
    assert in_site.count == 1


def test_site_filter_on_customers_and_pages(editor, other_site):
    customer = create_customer(editor, name="Acme")
    site = create_site(editor, name="Acme HQ", customer_id=customer.id)
    device = create_device(editor, name="acme-sw", kind="physical", site_id=site.id)
    outside = create_device(editor, name="zrh-sw", kind="physical", site_id=other_site.id)
    update_page(editor, get_item_page(editor, "device", device.id).id, body="runbook text")
    update_page(editor, get_item_page(editor, "device", outside.id).id, body="runbook text")
    create_page(editor, "Standalone runbook")

    assert labels(search(editor, "acme", site_id=site.id), "customer") == ["Acme"]
    assert labels(search(editor, "runbook", site_id=site.id), "page") == ["acme-sw"]
    assert labels(search(editor, "acme", site_id=other_site.id), "customer") == []


def test_unknown_site_and_long_query_are_errors(editor):
    with pytest.raises(ValidationError):
        search(editor, "x", site_id=999999)
    with pytest.raises(ValidationError):
        search(editor, "x" * (MAX_QUERY_LENGTH + 1))
    with pytest.raises(ValidationError):
        search(editor, "x", [" "])


def test_groups_report_more_than_they_show(editor, site):
    for number in range(MAX_HITS_PER_KIND + 3):
        create_device(editor, name=f"bulk-{number:02}", kind="physical", site_id=site.id)
    group = next(g for g in search(editor, "bulk").groups if g.kind == "device")
    assert group.count == MAX_HITS_PER_KIND + 3
    assert len(group.hits) == MAX_HITS_PER_KIND


def test_needs_a_role(editor, device, django_user_model):
    stranger = django_user_model.objects.create_user("stranger", password="another-long-password")
    with pytest.raises(PermissionDenied):
        search(stranger, "core")


def test_viewer_can_search(editor, device):
    viewer = create_user(editor, "viewer", "another-long-password", Role.VIEWER)
    assert labels(search(viewer, "core"), "device") == ["core-sw"]


def test_web_search_groups_and_links(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Basel")
    device = create_device(admin, name="core-sw", kind="physical", site_id=site.id)
    create_page(admin, "core <script>alert(1)</script>", "core text")

    response = logged_in_client.get("/search/", {"q": "core"})
    html = response.content.decode()
    assert response.status_code == 200
    assert f'href="/devices/{device.id}/"' in html
    assert "<script>alert(1)" not in html
    assert "2 results" in html


def test_web_header_has_the_search_box(logged_in_client):
    html = logged_in_client.get("/").content.decode()
    assert 'action="/search/"' in html


def test_web_search_keeps_the_query_escaped(logged_in_client):
    html = logged_in_client.get("/search/", {"q": '"><script>x'}).content.decode()
    assert '"><script>x' not in html


@pytest.mark.parametrize("site", ["abc", "9" * 40, "٣", "999999"])
def test_web_bad_site_is_an_error(logged_in_client, site):
    assert logged_in_client.get("/search/", {"q": "x", "site": site}).status_code == 400


def test_web_search_requires_login(db):
    response = Client().get("/search/", {"q": "x"})
    assert response.status_code == 302


def test_api_search(editor, device):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    add_tag(editor, "device", device.id, "prod")

    body = api.get("/api/search/", {"q": "core", "tag": "prod", "site": device.site_id}).json()
    assert body["total"] == 1
    assert body["groups"][0]["kind"] == "device"
    assert body["groups"][0]["hits"][0]["id"] == device.id


@pytest.mark.parametrize("params", [{"nope": "1"}, {"site": "abc"}, {"site": "999999"}, {"tag": " "}])
def test_api_bad_filters_use_the_error_shape(editor, params):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    response = api.get("/api/search/", {"q": "x", **params})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
