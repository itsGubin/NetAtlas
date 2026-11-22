import pytest
from django.contrib.auth import authenticate
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, ensure_initial_admin
from netatlas.addressing.services import (
    assign_ip_address,
    create_dhcp_range,
    create_subnet,
    create_vlan,
)
from netatlas.deletion.services import StaleDelete, confirm_delete, preview_delete
from netatlas.devices.services import (
    create_cable,
    create_device,
    create_device_type,
    create_interface,
    create_rack,
    delete_device,
    delete_interface,
    update_device,
)
from netatlas.pages.live_fields import LIVE_FIELDS, read_value
from netatlas.pages.models import MENTIONABLE_MODELS
from netatlas.pages.services import (
    create_page,
    editor_body,
    get_item_page,
    get_page,
    live_field_options,
    live_field_values,
    referenced_by,
    render_page,
    update_page,
)
from netatlas.places.services import create_customer, create_site


def link(kind, item_id):
    return f"/{kind}/{item_id}/"


def live(value):
    return f'<span class="live-field">{value}</span>'


@pytest.fixture
def editor(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def site(editor):
    return create_site(editor, name="Home")


@pytest.fixture
def device(editor, site):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    return create_device(editor, name="core-sw", kind="physical", site_id=site.id)


@pytest.fixture
def port(editor, device):
    return create_interface(editor, device.id, name="eth0", kind="physical")


def test_every_mentionable_kind_has_live_fields():
    assert set(LIVE_FIELDS) == set(MENTIONABLE_MODELS)


def test_every_live_field_can_be_read_on_every_kind(editor, site, device, port):
    other = create_interface(editor, device.id, name="eth1", kind="physical")
    cable = create_cable(editor, port.id, other.id, label="A-1")
    address = assign_ip_address(editor, port.id, "10.0.0.5")
    subnet = create_subnet(editor, cidr="10.0.1.0/24", site_id=site.id)
    items = {
        "customer": create_customer(editor, name="Acme").id,
        "site": site.id,
        "rack": create_rack(editor, name="R1", site_id=site.id, height=4).id,
        "device": device.id,
        "interface": port.id,
        "vlan": create_vlan(editor, number=10, site_id=site.id).id,
        "subnet": subnet.id,
        "dhcp-range": create_dhcp_range(editor, subnet.id, "10.0.1.10", "10.0.1.20").id,
        "ip-address": address.id,
        "cable": cable.id,
        "device-type": create_device_type(editor, make="Acme", model_name="SW").id,
    }

    for kind, item_id in items.items():
        item = MENTIONABLE_MODELS[kind].objects.get(pk=item_id)
        for field in LIVE_FIELDS[kind]:
            assert all(isinstance(v, str) for v in read_value(item, kind, field))
    assert set(items) == set(LIVE_FIELDS)


def test_device_field_shows_all_ips_and_interface_field_only_its_own(editor, device, port):
    other = create_interface(editor, device.id, name="eth1", kind="physical")
    assign_ip_address(editor, other.id, "10.0.0.10")
    assign_ip_address(editor, port.id, "10.0.0.9")
    page = create_page(
        editor,
        "Doc",
        f"@{{device:{device.id}.ip-addresses}} / @{{interface:{port.id}.ip-addresses}}",
    )

    html = render_page(editor, page.id, link)

    assert live("10.0.0.9, 10.0.0.10") in html
    assert live("10.0.0.9") in html


def test_value_rerenders_and_follows_renames_without_changing_the_page(editor, device):
    body = f"@{{device:{device.id}.name}}"
    page = create_page(editor, "Doc", body)
    assert live("core-sw") in render_page(editor, page.id, link)

    update_device(editor, device.id, name="renamed")

    assert live("renamed") in render_page(editor, page.id, link)
    assert get_page(editor, page.id).body == body


def test_empty_value_shows_a_dash(editor, device):
    page = create_page(editor, "Doc", f"@{{device:{device.id}.rack}}")

    assert live("—") in render_page(editor, page.id, link)


def test_works_on_item_pages(editor, site, device):
    page = get_item_page(editor, "site", site.id)
    update_page(editor, page.id, body=f"@{{device:{device.id}.name}}")

    assert live("core-sw") in render_page(editor, page.id, link)


def test_deleted_item_shows_a_broken_marker(editor, device, port):
    page = create_page(editor, "Doc", f"@{{interface:{port.id}.name}}")

    delete_interface(editor, port.id)

    assert 'class="broken-live-field"' in render_page(editor, page.id, link)
    assert editor_body(editor, page.id) == f"@{{interface:{port.id}.name}}"
    value = live_field_values(editor, page.id)[0]
    assert (value.is_broken, value.item_label, value.values) == (True, None, [])


def test_editor_shows_names_and_saving_stores_ids(editor, device):
    stored = f"Name: @{{device:{device.id}.name}}"
    page = create_page(editor, "Doc", stored)
    shown = editor_body(editor, page.id)
    assert shown == f"Name: @{{core-sw: Name}}(device:{device.id}.name)"

    update_device(editor, device.id, name="renamed")
    update_page(editor, page.id, body=shown)

    assert get_page(editor, page.id).body == stored


@pytest.mark.parametrize(
    "template", ["`@{{device:{id}.name}}`", "```\n@{{device:{id}.name}}\n```\n", "@{{device:{id}.nope}}"]
)
def test_code_and_unknown_fields_stay_plain_text(editor, device, template):
    body = template.format(id=device.id)
    page = create_page(editor, "Doc", body)

    assert "live-field" not in render_page(editor, page.id, link)
    assert live_field_values(editor, page.id) == []
    assert editor_body(editor, page.id) == body


def test_value_cannot_inject_markup(editor, site):
    item = create_device(editor, name="<script>x", kind="physical", site_id=site.id)
    page = create_page(editor, "Doc", f"@{{device:{item.id}.name}}")

    assert "<script" not in render_page(editor, page.id, link)


def test_live_field_is_not_a_mention(editor, device):
    create_page(editor, "Doc", f"@{{device:{device.id}.name}}")

    assert referenced_by(editor, "device", device.id) == []


def test_options_match_every_word(editor, device):
    options = live_field_options(editor, "core ip")

    assert [(o.kind, o.field) for o in options] == [("device", "ip-addresses")]
    assert options[0].token == f"@{{core-sw: IP Addresses}}(device:{device.id}.ip-addresses)"


def test_preview_lists_live_fields_that_will_break(editor, device, port):
    page = create_page(
        editor,
        "Doc",
        f"@{{device:{device.id}.name}} @{{interface:{port.id}.name}} `@{{device:{device.id}.kind}}`",
    )
    own = get_item_page(editor, "device", device.id)
    update_page(editor, own.id, body=f"@{{device:{device.id}.name}}")

    preview = preview_delete(editor, "device", device.id)

    assert [(i.id, i.label) for i in preview.broken_live_fields] == [
        (page.id, "Doc"),
        (page.id, "Doc"),
    ]
    assert "core-sw, Name" in preview.broken_live_fields[0].effect
    assert "core-sw eth0, Name" in preview.broken_live_fields[1].effect


def test_live_field_added_after_the_preview_makes_the_delete_stale(editor, device):
    preview = preview_delete(editor, "device", device.id)
    create_page(editor, "Doc", f"@{{device:{device.id}.name}}")

    with pytest.raises(StaleDelete):
        confirm_delete(editor, "device", device.id, preview.fingerprint)


def test_web_delete_confirmation_warns(client_with_role):
    client = client_with_role(Role.EDITOR)
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    item = create_device(admin, name="core-sw", kind="physical", site_id=site.id)
    create_page(admin, "Wi-Fi", f"@{{device:{item.id}.name}}")

    html = client.get(f"/devices/{item.id}/delete/").content.decode()

    assert "Live Fields that will break" in html
    assert "Wi-Fi" in html


def test_web_page_and_options(client_with_role):
    client = client_with_role(Role.EDITOR)
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    item = create_device(admin, name="core-sw", kind="physical", site_id=site.id)
    page = create_page(admin, "Wi-Fi", f"@{{device:{item.id}.name}}")

    assert live("core-sw") in client.get(f"/pages/{page.id}/").content.decode()
    options = client.get("/pages/live-field-options/", {"q": "core kind"}).content.decode()
    assert f"(device:{item.id}.kind)" in options


def test_api_values_options_and_preview(editor, device):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 'tests').secret}")
    page = create_page(editor, "Doc", f"@{{device:{device.id}.name}}")

    values = api.get(f"/api/pages/{page.id}/live-fields/").json()["results"]
    assert (values[0]["values"], values[0]["is_broken"]) == (["core-sw"], False)
    assert api.get("/api/live-field-options/", {"q": "core kind"}).json()[0]["field"] == "kind"
    assert api.get("/api/live-field-options/", {"typo": "x"}).status_code == 400
    preview = api.get(f"/api/delete-preview/device/{device.id}/").json()
    assert preview["broken_live_fields"][0]["id"] == page.id

    delete_device(editor, device.id)
    values = api.get(f"/api/pages/{page.id}/live-fields/").json()["results"]
    assert values[0]["is_broken"] is True
