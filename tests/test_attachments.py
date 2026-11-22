import re

import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin
from netatlas.deletion.services import preview_delete
from netatlas.devices.services import create_device, delete_device
from netatlas.history.services import child_history, item_history
from netatlas.pages.attachments import (
    add_attachment,
    attachment_response,
    delete_attachment,
    get_attachment,
    list_attachments,
)
from netatlas.pages.models import Attachment
from netatlas.pages.services import (
    create_page,
    delete_page,
    get_item_page,
    render_page,
    update_page,
)
from netatlas.places.services import create_site
from tests.helpers import confirm_delete

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32
GIF = b"GIF89a" + b"\0" * 32
WEBP = b"RIFF\x10\0\0\0WEBPVP8 " + b"\0" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><script>alert(1)</script></html>"


def upload(name="rack.png", content=PNG, content_type="image/png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def link(kind, item_id):
    return f"/{kind}/{item_id}/"


def body_of(response):
    return b"".join(response.streaming_content)


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def editor(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def viewer(editor):
    return create_user(editor, "viewer", "another-long-password", Role.VIEWER)


@pytest.fixture
def page(editor):
    return create_page(editor, "Wi-Fi", "text")


def stored_name(attachment):
    return Attachment.objects.get(pk=attachment.id).storage_name


@pytest.mark.parametrize(
    "content, expected",
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (WEBP, "image/webp"),
    ],
)
def test_raster_images_are_recognised_by_their_bytes(editor, page, content, expected):
    found = add_attachment(editor, page.id, upload("photo.bin", content, "text/html"))
    assert found.content_type == expected
    assert found.is_image
    assert found.markdown == f"![photo.bin](attachment:{found.id})"


@pytest.mark.parametrize(
    "name, content, claimed",
    [
        ("evil.svg", SVG, "image/svg+xml"),
        ("evil.html", HTML, "text/html"),
        ("evil.png", HTML, "image/png"),
    ],
)
def test_anything_else_is_only_ever_a_download(editor, page, name, content, claimed):
    found = add_attachment(editor, page.id, upload(name, content, claimed))
    assert found.content_type == "application/octet-stream"
    assert not found.is_image

    response = attachment_response(editor, found.id)
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert body_of(response) == content


def test_image_is_served_inline(editor, page):
    found = add_attachment(editor, page.id, upload())
    response = attachment_response(editor, found.id)
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"].startswith("inline")
    assert body_of(response) == PNG


def test_file_is_stored_under_a_random_name(editor, page):
    found = add_attachment(editor, page.id, upload("../../etc/passwd"))
    name = stored_name(found)
    assert re.fullmatch(r"attachments/[0-9a-f]{32}", name)
    assert found.filename == "passwd"


def test_uploads_are_limited(editor, page, settings):
    settings.MAX_ATTACHMENT_BYTES = 10
    with pytest.raises(ValidationError):
        add_attachment(editor, page.id, upload(content=b"x" * 11))
    with pytest.raises(ValidationError):
        add_attachment(editor, page.id, upload(content=b""))
    with pytest.raises(ValidationError):
        add_attachment(editor, page.id, None)
    assert not list_attachments(editor, page.id)


def test_oversized_upload_stops_before_the_view_gets_a_file(
    logged_in_client, settings, media
):
    settings.MAX_ATTACHMENT_BYTES = 1024
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Wi-Fi")
    response = logged_in_client.post(
        f"/pages/{page.id}/attachments/", {"file": upload(content=PNG + b"x" * 5000)}
    )
    assert response.status_code == 400
    assert "at most" in response.content.decode()
    assert not list(media.rglob("*.*"))
    assert not list_attachments(admin, page.id)


def test_failed_save_leaves_no_file(editor, page, media, monkeypatch):
    def refuse(*args, **kwargs):
        raise ValidationError("refused")

    monkeypatch.setattr("netatlas.pages.attachments.create_item", refuse)
    with pytest.raises(ValidationError):
        add_attachment(editor, page.id, upload())
    assert not [path for path in media.rglob("*") if path.is_file()]


def test_viewer_reads_but_cannot_change(editor, viewer, page):
    found = add_attachment(editor, page.id, upload())
    assert get_attachment(viewer, found.id).filename == "rack.png"
    assert body_of(attachment_response(viewer, found.id)) == PNG
    with pytest.raises(PermissionDenied):
        add_attachment(viewer, page.id, upload())
    with pytest.raises(PermissionDenied):
        delete_attachment(viewer, found.id)


def test_download_needs_login(editor, page):
    found = add_attachment(editor, page.id, upload())
    response = Client().get(f"/attachments/{found.id}/")
    assert response.status_code == 302
    assert "/login/" in response["Location"]
    assert Client().get(f"/api/attachments/{found.id}/download/").status_code == 401


def test_image_embeds_inline(editor, page):
    found = add_attachment(editor, page.id, upload())
    update_page(editor, page.id, body=f"![Rack <front>](attachment:{found.id})")
    html = render_page(editor, page.id, link)
    assert f'<img src="/attachment/{found.id}/" alt="Rack &lt;front&gt;">' in html


def test_other_file_embed_is_a_link(editor, page):
    found = add_attachment(editor, page.id, upload("manual.pdf", b"%PDF-1.7", "application/pdf"))
    update_page(editor, page.id, body=f"![](attachment:{found.id})")
    html = render_page(editor, page.id, link)
    assert f'<a href="/attachment/{found.id}/">manual.pdf</a>' in html
    assert "<img" not in html


def test_outside_images_still_render(editor, page):
    update_page(editor, page.id, body="![x](https://example.test/a.png)")
    assert 'src="https://example.test/a.png"' in render_page(editor, page.id, link)


def test_delete_removes_file_after_commit_and_records_history(
    editor, page, django_capture_on_commit_callbacks
):
    found = add_attachment(editor, page.id, upload())
    name = stored_name(found)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        delete_attachment(editor, found.id)
    assert default_storage.exists(name)
    for callback in callbacks:
        callback()
    assert not default_storage.exists(name)

    actions = [entry.action for entry in item_history(editor, "attachment", found.id)]
    assert actions == ["deleted", "created"]
    assert [e.action for e in child_history(editor, "attachment", "page", page.id)] == actions


def test_attachments_go_with_their_page(editor, page, django_capture_on_commit_callbacks):
    found = add_attachment(editor, page.id, upload())
    name = stored_name(found)
    with django_capture_on_commit_callbacks(execute=True):
        delete_page(editor, page.id)
    assert not Attachment.objects.filter(pk=found.id).exists()
    assert not default_storage.exists(name)
    assert item_history(editor, "attachment", found.id)[0].action == "deleted"


def test_attachments_go_with_an_item_page(editor, django_capture_on_commit_callbacks):
    site = create_site(editor, name="Home")
    device = create_device(editor, name="core", kind="physical", site_id=site.id)
    page = get_item_page(editor, "device", device.id)
    found = add_attachment(editor, page.id, upload())
    name = stored_name(found)
    with django_capture_on_commit_callbacks(execute=True):
        delete_device(editor, device.id)
    assert not default_storage.exists(name)


def test_delete_preview_keeps_the_file_and_shows_broken_embeds(
    editor, page, django_capture_on_commit_callbacks
):
    found = add_attachment(editor, page.id, upload())
    other = create_page(editor, "Other", f"![a](attachment:{found.id})")
    name = stored_name(found)

    with django_capture_on_commit_callbacks(execute=True):
        preview = preview_delete(editor, "attachment", found.id)

    assert default_storage.exists(name)
    assert [i.id for i in preview.broken_embeds] == [other.id]
    assert "rack.png" in preview.broken_embeds[0].effect

    delete_attachment(editor, found.id)
    assert "broken-attachment" in render_page(editor, other.id, link)


def test_missing_file_is_a_404(editor, page):
    found = add_attachment(editor, page.id, upload())
    default_storage.delete(stored_name(found))
    with pytest.raises(Http404):
        attachment_response(editor, found.id)


def test_web_upload_list_download_and_delete(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Wi-Fi")
    path = f"/pages/{page.id}/"

    response = logged_in_client.post(f"{path}attachments/", {"file": upload()})
    assert response.status_code == 302
    found = list_attachments(admin, page.id)[0]

    html = logged_in_client.get(path).content.decode()
    assert f'href="/attachments/{found.id}/"' in html
    assert 'enctype="multipart/form-data"' in html
    assert body_of(logged_in_client.get(f"/attachments/{found.id}/")) == PNG

    response = confirm_delete(logged_in_client, f"/attachments/{found.id}/delete/")
    assert response.status_code == 302
    assert response["Location"] == path
    assert not list_attachments(admin, page.id)
    assert "rack.png" in logged_in_client.get(path).content.decode()


def test_web_viewer_sees_no_upload_form(client_with_role):
    client = client_with_role(Role.VIEWER)
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Wi-Fi")
    assert "multipart/form-data" not in client.get(f"/pages/{page.id}/").content.decode()
    response = client.post(f"/pages/{page.id}/attachments/", {"file": upload()})
    assert response.status_code == 403


def test_api_upload_download_and_delete(editor, page):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    url = f"/api/pages/{page.id}/attachments/"

    created = api.post(url, {"file": upload()})
    assert created.status_code == 201
    found = created.json()
    assert found["content_type"] == "image/png"
    assert "storage_name" not in found
    assert api.get(url).json()["count"] == 1

    download = api.get(
        f"/api/attachments/{found['id']}/download/", HTTP_ACCEPT="application/octet-stream"
    )
    assert download.status_code == 200
    assert body_of(download) == PNG

    assert api.delete(f"/api/attachments/{found['id']}/").status_code == 204
    assert api.get(f"/api/attachments/{found['id']}/").status_code == 404


def test_api_missing_file_uses_the_error_shape(editor, page):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    response = api.post(f"/api/pages/{page.id}/attachments/", {})
    assert response.status_code == 400
    assert "file" in response.json()["error"]["fields"]
