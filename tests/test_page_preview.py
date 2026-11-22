import json

import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin
from netatlas.devices.services import create_device
from netatlas.history.services import item_history
from netatlas.pages.services import (
    create_page,
    get_item_page,
    get_page,
    list_page_templates,
    preview_page,
    update_page,
    update_page_template,
)
from netatlas.places.services import create_site

TWO_SECTIONS = "# Runbook\n\n{{section:Access}}\n\nName: @{this.name}\n\n{{section:Notes}}\n"


def link(kind, item_id):
    return f"/{kind}/{item_id}/"


@pytest.fixture
def editor(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def device(editor):
    site = create_site(editor, name="Home")
    return create_device(editor, name="core-sw", kind="physical", site_id=site.id)


def use_two_sections(editor):
    template = next(
        t for t in list_page_templates(editor) if t.is_default and t.item_kind == "device"
    )
    update_page_template(editor, template.id, body=TWO_SECTIONS)


def test_new_page_preview_renders_markdown_mentions_and_live_fields(editor, device):
    html = preview_page(
        editor,
        link,
        body=f"**bold** @[x](device:{device.id}) @{{x}}(device:{device.id}.name)",
    )
    assert "<strong>bold</strong>" in html
    assert f'<a href="/device/{device.id}/">core-sw</a>' in html
    assert '<span class="live-field">core-sw</span>' in html


def test_raw_html_is_escaped(editor):
    html = preview_page(editor, link, body="<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_preview_saves_nothing(editor):
    page = create_page(editor, "Wi-Fi", "saved")
    before = len(item_history(editor, "page", page.id))
    html = preview_page(editor, link, page_id=page.id, body="draft")
    assert "draft" in html
    assert get_page(editor, page.id).body == "saved"
    assert len(item_history(editor, "page", page.id)) == before


def test_item_page_preview_uses_its_template_and_sections(editor, device):
    use_two_sections(editor)
    page = get_item_page(editor, "device", device.id)
    html = preview_page(
        editor, link, page_id=page.id, sections={"Access": "ssh here", "Notes": "note"}
    )
    assert "<h1>Runbook</h1>" in html
    assert "ssh here" in html
    assert '<span class="live-field">core-sw</span>' in html
    assert get_page(editor, page.id).sections == {}


def test_section_text_is_not_template_syntax_in_a_preview(editor, device):
    page = get_item_page(editor, "device", device.id)
    html = preview_page(editor, link, page_id=page.id, sections={"Notes": "@{this.name}"})
    assert "@{this.name}" in html


def test_unknown_section_is_an_error(editor, device):
    page = get_item_page(editor, "device", device.id)
    with pytest.raises(ValidationError):
        preview_page(editor, link, page_id=page.id, sections={"Nope": "x"})


def test_standalone_page_has_no_sections(editor):
    page = create_page(editor, "Wi-Fi")
    with pytest.raises(ValidationError):
        preview_page(editor, link, page_id=page.id, sections={"Notes": "x"})
    with pytest.raises(ValidationError):
        preview_page(editor, link, sections={"Notes": "x"})


def test_unknown_page_is_a_404(editor):
    with pytest.raises(Http404):
        preview_page(editor, link, page_id=999999, body="x")


def test_viewer_cannot_preview(editor):
    viewer = create_user(editor, "viewer", "another-long-password", Role.VIEWER)
    with pytest.raises(PermissionDenied):
        preview_page(viewer, link, body="x")


def test_web_preview_for_a_new_page(logged_in_client):
    response = logged_in_client.post("/pages/preview/", {"body": "**bold**"})
    assert response.status_code == 200
    assert "<strong>bold</strong>" in response.content.decode()
    assert "<html" not in response.content.decode()


def test_web_preview_for_item_page_sections(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    device = create_device(admin, name="core-sw", kind="physical", site_id=site.id)
    use_two_sections(admin)
    page = get_item_page(admin, "device", device.id)
    response = logged_in_client.post(
        "/pages/preview/",
        {
            "page_id": page.id,
            "section_name": ["Access", "Notes"],
            "section_content": ["*ssh*", "note"],
        },
    )
    assert "<em>ssh</em>" in response.content.decode()


def test_web_preview_shows_a_bad_draft_without_failing(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Wi-Fi")
    response = logged_in_client.post(
        "/pages/preview/",
        {"page_id": page.id, "section_name": ["Notes"], "section_content": ["x"]},
    )
    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


@pytest.mark.parametrize("page_id", ["abc", "9" * 40, "٣", "999999"])
def test_web_preview_of_a_bad_page_id_is_a_404(logged_in_client, page_id):
    response = logged_in_client.post("/pages/preview/", {"page_id": page_id, "body": "x"})
    assert response.status_code == 404


def test_web_preview_needs_an_editor_and_csrf(client_with_role):
    client = client_with_role(Role.VIEWER)
    assert client.post("/pages/preview/", {"body": "x"}).status_code == 403
    assert Client().post("/pages/preview/", {"body": "x"}).status_code == 302
    assert Client(enforce_csrf_checks=True).post("/pages/preview/", {"body": "x"}).status_code == 403


def test_editors_contain_the_preview(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Wi-Fi", "text")
    for path in ("/pages/", f"/pages/{page.id}/edit/"):
        html = logged_in_client.get(path).content.decode()
        assert 'id="page-preview"' in html
        assert 'hx-post="/pages/preview/"' in html


def test_api_preview(editor, device):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    page = get_item_page(editor, "device", device.id)
    body = {"page_id": page.id, "sections": {"Notes": "**bold**"}}
    response = api.post("/api/pages/preview/", json.dumps(body), content_type="application/json")
    assert response.status_code == 200
    assert "<strong>bold</strong>" in response.json()["html"]
    assert get_page(editor, page.id).sections == {}


def test_api_preview_errors_use_the_error_shape(editor):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    response = api.post(
        "/api/pages/preview/",
        json.dumps({"sections": {"Notes": "x"}}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_item_page_view_lists_pages_mentioning_its_item(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    device = create_device(admin, name="core-sw", kind="physical", site_id=site.id)
    mentioning = create_page(admin, "Runbook", f"@[device:{device.id}]")
    page = get_item_page(admin, "device", device.id)

    html = logged_in_client.get(f"/pages/{page.id}/").content.decode()
    assert "Referenced by" in html
    assert f'<a href="/pages/{mentioning.id}/">Runbook</a>' in html


def test_standalone_page_view_has_no_references_section(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Wi-Fi")
    assert "Referenced by" not in logged_in_client.get(f"/pages/{page.id}/").content.decode()


def test_template_screens_show_kind_names(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    template = next(t for t in list_page_templates(admin) if t.item_kind == "vlan")
    assert "Default: VLAN" in logged_in_client.get("/page-templates/").content.decode()
    detail = logged_in_client.get(f"/page-templates/{template.id}/").content.decode()
    assert "Default for VLAN" in detail


def test_update_after_preview_still_saves(editor):
    page = create_page(editor, "Wi-Fi", "old")
    preview_page(editor, link, page_id=page.id, body="new")
    update_page(editor, page.id, body="new")
    assert get_page(editor, page.id).body == "new"
