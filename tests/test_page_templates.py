import json
import re

import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin
from netatlas.deletion.services import preview_delete
from netatlas.devices.services import (
    create_device,
    create_device_type,
    delete_device_type,
)
from netatlas.history.services import item_history
from netatlas.pages import skeleton
from netatlas.pages.services import (
    UnconfirmedSectionRemoval,
    create_page_template,
    delete_page_template,
    editor_sections,
    get_item_page,
    get_page,
    list_page_templates,
    preview_template_change,
    referenced_by,
    render_page,
    update_page,
    update_page_template,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

TWO_SECTIONS = "# Runbook\n\n{{section:Access}}\n\n## Name\n\n@{this.name}\n\n{{section:Notes}}\n"


@pytest.fixture
def editor(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def viewer(editor):
    return create_user(editor, "viewer", "another-long-password", Role.VIEWER)


@pytest.fixture
def site(editor):
    return create_site(editor, name="Home")


@pytest.fixture
def device_type(editor):
    return create_device_type(editor, make="Acme", model_name="S1", rack_height=1)


@pytest.fixture
def device(editor, site):
    return create_device(editor, name="core", kind="physical", site_id=site.id)


def default(editor, kind):
    return next(
        t for t in list_page_templates(editor) if t.is_default and t.item_kind == kind
    )


def page_of(editor, device):
    return get_item_page(editor, "device", device.id)


def test_each_kind_has_one_default(editor):
    kinds = [t.item_kind for t in list_page_templates(editor) if t.is_default]
    assert sorted(kinds) == ["customer", "device", "rack", "site", "subnet", "vlan"]


def test_a_new_items_page_renders_from_its_template(editor, site):
    template = default(editor, "device")
    update_page_template(editor, template.id, body=TWO_SECTIONS)
    device = create_device(editor, name="edge", kind="physical", site_id=site.id)

    page = page_of(editor, device)
    assert page.template_id == template.id
    assert f"@{{device:{device.id}.name}}" in page.body
    assert "edge" in render_page(editor, page.id, lambda k, i: f"/{k}/{i}/")


def test_template_edit_reaches_every_page_and_keeps_section_content(editor, device):
    template = default(editor, "device")
    page = page_of(editor, device)
    update_page(editor, page.id, sections={"Notes": "keep me"})

    update_page_template(editor, template.id, body=TWO_SECTIONS)

    changed = get_page(editor, page.id)
    assert changed.sections == {"Notes": "keep me"}
    assert changed.body.startswith("# Runbook")
    assert "keep me" in changed.body


def test_section_text_is_never_read_as_template_syntax(editor, device):
    page = page_of(editor, device)
    text = "{{section:Other}} @{this.name}"
    update_page(editor, page.id, sections={"Notes": text})
    assert get_page(editor, page.id).body == text


def test_mentions_in_sections_are_tracked(editor, device, site):
    page = page_of(editor, device)
    update_page(editor, page.id, sections={"Notes": f"@[site:{site.id}]"})
    assert page.id in [p.id for p in referenced_by(editor, "site", site.id)]


def test_removing_a_section_needs_the_shown_count(editor, device):
    template = default(editor, "device")
    update_page_template(editor, template.id, body=TWO_SECTIONS)
    page = page_of(editor, device)
    update_page(editor, page.id, sections={"Access": "ssh", "Notes": "stay"})
    without = TWO_SECTIONS.replace("{{section:Access}}\n\n", "")

    assert [(r.name, r.page_count) for r in preview_template_change(editor, template.id, without)] == [
        ("Access", 1)
    ]
    with pytest.raises(UnconfirmedSectionRemoval):
        update_page_template(editor, template.id, body=without)
    with pytest.raises(UnconfirmedSectionRemoval):
        update_page_template(editor, template.id, body=without, confirmed_removals={"Access": 2})
    assert get_page(editor, page.id).sections["Access"] == "ssh"

    update_page_template(editor, template.id, body=without, confirmed_removals={"Access": 1})

    changed = get_page(editor, page.id)
    assert changed.sections == {"Notes": "stay"}
    assert "ssh" not in changed.body
    recorded = [
        change.old
        for entry in item_history(editor, "page", page.id)
        for change in entry.changes
        if change.field == "sections"
    ]
    assert {"Access": "ssh", "Notes": "stay"} in recorded


def test_removing_an_empty_section_needs_no_confirmation(editor, device):
    template = default(editor, "device")
    update_page_template(editor, template.id, body=TWO_SECTIONS)
    update_page_template(editor, template.id, body="{{section:Notes}}")
    assert default(editor, "device").sections == ["Notes"]


def test_override_applies_to_its_device_type_only(editor, site, device, device_type):
    typed = create_device(
        editor, name="typed", kind="physical", site_id=site.id, device_type_id=device_type.id
    )
    update_page(editor, page_of(editor, typed).id, sections={"Notes": "mine"})

    override = create_page_template(
        editor, "Acme", device_type.id, "# Acme\n\n{{section:Notes}}"
    )

    assert page_of(editor, typed).template_id == override.id
    assert page_of(editor, typed).body == "# Acme\n\nmine"
    assert page_of(editor, device).template_id == default(editor, "device").id
    assert default(editor, "device").page_count == 1
    assert override.page_count == 1


def test_one_override_per_device_type(editor, device_type):
    create_page_template(editor, "First", device_type.id)
    with pytest.raises(ValidationError):
        create_page_template(editor, "Second", device_type.id)


def test_moving_to_another_template_loses_nothing(editor, site, device_type):
    device = create_device(
        editor, name="typed", kind="physical", site_id=site.id, device_type_id=device_type.id
    )
    update_page(editor, page_of(editor, device).id, sections={"Notes": "mine"})
    override = create_page_template(editor, "Acme", device_type.id, "{{section:Runbook}}")

    moved = page_of(editor, device)
    assert moved.template_id == override.id
    assert moved.body == "## Notes\n\nmine"
    assert [(s.name, s.in_template) for s in editor_sections(editor, moved.id)] == [
        ("Runbook", True),
        ("Notes", False),
    ]

    delete_page_template(editor, override.id)
    assert page_of(editor, device).body == "mine"


@pytest.mark.parametrize("remove", ["override", "device-type"])
def test_deleting_an_override_returns_devices_to_the_default(editor, site, device_type, remove):
    device = create_device(
        editor, name="typed", kind="physical", site_id=site.id, device_type_id=device_type.id
    )
    override = create_page_template(editor, "Acme", device_type.id, "# Acme\n\n{{section:Notes}}")
    update_page(editor, page_of(editor, device).id, sections={"Notes": "mine"})

    if remove == "override":
        delete_page_template(editor, override.id)
    else:
        delete_device_type(editor, device_type.id)

    page = page_of(editor, device)
    assert page.template_id == default(editor, "device").id
    assert page.body == "mine"


def test_default_cannot_be_deleted(editor):
    template = default(editor, "site")
    with pytest.raises(ValidationError):
        delete_page_template(editor, template.id)
    assert preview_delete(editor, "page-template", template.id).blocked


@pytest.mark.parametrize(
    "body",
    [
        "{{section:A}}\n{{section:A}}",
        "{{section: }}",
        "@{this.nonsense}",
        "{{section:" + "x" * (skeleton.MAX_SECTION_NAME_LENGTH + 1) + "}}",
    ],
)
def test_invalid_templates_are_refused(editor, body):
    with pytest.raises(ValidationError):
        update_page_template(editor, default(editor, "device").id, body=body)


def test_viewer_cannot_change_templates(viewer, editor, device_type):
    template = default(editor, "device")
    with pytest.raises(PermissionDenied):
        update_page_template(viewer, template.id, body="x")
    with pytest.raises(PermissionDenied):
        create_page_template(viewer, "x", device_type.id)
    assert list_page_templates(viewer)


def test_body_is_refused_once_a_page_has_several_sections(editor, device):
    update_page_template(editor, default(editor, "device").id, body=TWO_SECTIONS)
    page = page_of(editor, device)
    with pytest.raises(ValidationError):
        update_page(editor, page.id, body="flat")
    with pytest.raises(ValidationError):
        update_page(editor, page.id, sections={"Unknown": "x"})


def test_standalone_page_has_no_sections(editor):
    from netatlas.pages.services import create_page

    page = create_page(editor, "Wi-Fi", "text")
    with pytest.raises(ValidationError):
        update_page(editor, page.id, sections={"Notes": "x"})


def test_web_warns_before_removing_a_section(logged_in_client, db):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    device = create_device(admin, name="core", kind="physical", site_id=site.id)
    template = default(admin, "device")
    update_page_template(admin, template.id, body=TWO_SECTIONS)
    page = page_of(admin, device)
    update_page(admin, page.id, sections={"Access": "ssh"})
    path = f"/page-templates/{template.id}/"
    without = TWO_SECTIONS.replace("{{section:Access}}\n\n", "")

    warned = logged_in_client.post(path, {"name": template.name, "body": without})
    assert warned.status_code == 409
    html = warned.content.decode()
    assert "Access: content on 1 Page is deleted" in html
    assert get_page(admin, page.id).sections == {"Access": "ssh"}

    confirmed = re.search(r'name="confirmed_removals" value="([^"]+)"', html).group(1)
    confirmed = confirmed.replace("&quot;", '"')
    done = logged_in_client.post(
        path, {"name": template.name, "body": without, "confirmed_removals": confirmed}
    )
    assert done.status_code == 302
    assert get_page(admin, page.id).sections == {}


def test_web_edits_an_item_page_by_section(logged_in_client, db):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    device = create_device(admin, name="core", kind="physical", site_id=site.id)
    update_page_template(admin, default(admin, "device").id, body=TWO_SECTIONS)
    page = page_of(admin, device)

    form = logged_in_client.get(f"/pages/{page.id}/edit/").content.decode()
    assert form.count('name="section_content"') == 2

    response = logged_in_client.post(
        f"/pages/{page.id}/edit/",
        {"section_name": ["Access", "Notes"], "section_content": ["ssh", "note"]},
    )
    assert response.status_code == 302
    assert get_page(admin, page.id).sections == {"Access": "ssh", "Notes": "note"}


def test_web_creates_and_deletes_an_override(logged_in_client, db):
    admin = authenticate(username="root", password="correct-horse-battery")
    device_type = create_device_type(admin, make="Acme", model_name="S1", rack_height=1)

    response = logged_in_client.post(
        "/page-templates/",
        {"name": "Acme", "device_type_id": device_type.id, "body": "{{section:Notes}}"},
    )
    assert response.status_code == 302
    path = response["Location"]
    assert logged_in_client.get(path).status_code == 200

    assert confirm_delete(logged_in_client, f"{path}delete/").status_code == 302
    assert logged_in_client.get(path).status_code == 404


def test_web_refuses_a_bad_device_type(logged_in_client):
    for value in ["abc", "99999999", "9" * 40, "٣"]:
        response = logged_in_client.post(
            "/page-templates/", {"name": "x", "device_type_id": value, "body": ""}
        )
        assert response.status_code == 400


def test_api_confirms_section_removal(editor, device, client):
    token = create_token(editor, "t")
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token.secret}"}
    template = default(editor, "device")
    update_page_template(editor, template.id, body=TWO_SECTIONS)
    page = page_of(editor, device)

    changed = client.patch(
        f"/api/pages/{page.id}/",
        json.dumps({"sections": {"Access": "ssh"}}),
        content_type="application/json",
        **headers,
    )
    assert changed.status_code == 200
    assert changed.json()["sections"] == {"Access": "ssh"}

    without = TWO_SECTIONS.replace("{{section:Access}}\n\n", "")
    url = f"/api/page-templates/{template.id}/"
    preview = client.post(
        f"{url}preview/", json.dumps({"body": without}), content_type="application/json", **headers
    )
    assert preview.json() == [{"name": "Access", "page_count": 1}]

    refused = client.patch(
        url, json.dumps({"body": without}), content_type="application/json", **headers
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "confirmation_required"

    done = client.patch(
        url,
        json.dumps({"body": without, "confirmed_removals": {"Access": 1}}),
        content_type="application/json",
        **headers,
    )
    assert done.status_code == 200
    assert get_page(editor, page.id).sections == {}
