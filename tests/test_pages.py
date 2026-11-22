import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin
from netatlas.addressing.services import create_subnet, create_vlan
from netatlas.deletion.services import preview_delete
from netatlas.devices.services import (
    create_device,
    create_interface,
    create_rack,
    delete_device,
    delete_interface,
    update_device,
)
from netatlas.history.models import allow_tracked_writes
from netatlas.history.services import delete_item, item_history
from netatlas.pages.models import Page
from netatlas.pages.services import (
    create_page,
    delete_page,
    editor_body,
    get_item_page,
    get_page,
    list_pages,
    mention_options,
    referenced_by,
    render_page,
    update_page,
)
from netatlas.places.services import create_customer, create_site, ensure_default_site
from netatlas.tagging.services import add_tag, filter_by_tags
from tests.helpers import confirm_delete


def link(kind, item_id):
    return f"/{kind}/{item_id}/"


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
def device(editor, site):
    return create_device(editor, name="core-sw", kind="physical", site_id=site.id)


def test_every_item_kind_gets_exactly_one_item_page(editor, site, device):
    items = {
        "customer": create_customer(editor, name="Acme").id,
        "site": site.id,
        "device": device.id,
        "rack": create_rack(editor, name="R1", site_id=site.id, height=4).id,
        "vlan": create_vlan(editor, number=10, site_id=site.id).id,
        "subnet": create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id).id,
    }

    for kind, item_id in items.items():
        page = get_item_page(editor, kind, item_id)
        assert (page.item_kind, page.item_id) == (kind, item_id)
        assert Page.objects.filter(item_kind=kind, item_id=item_id).count() == 1
        assert item_history(editor, "page", page.id)[-1].action == "created"


def test_default_site_gets_an_item_page_credited_to_the_system(editor):
    ensure_default_site()
    page = Page.objects.get(item_kind="site")

    assert item_history(editor, "page", page.pk)[0].actor == "system"


def test_kinds_without_item_pages_have_none(editor, device):
    port = create_interface(editor, device.id, name="eth0", kind="physical")

    with pytest.raises(Http404):
        get_item_page(editor, "interface", port.id)


def test_item_page_follows_the_item_name_and_is_deleted_with_it(editor, device):
    page = get_item_page(editor, "device", device.id)
    update_device(editor, device.id, name="renamed")
    assert get_page(editor, page.id).title == "renamed"

    preview = preview_delete(editor, "device", device.id)
    assert [(i.kind, i.id, i.label) for i in preview.deleted] == [("page", page.id, "renamed")]
    assert get_page(editor, page.id).id == page.id

    delete_device(editor, device.id)

    with pytest.raises(Http404):
        get_page(editor, page.id)
    assert item_history(editor, "page", page.id)[0].action == "deleted"


def test_item_page_cannot_be_deleted_or_retitled_on_its_own(editor, device):
    page = get_item_page(editor, "device", device.id)

    with pytest.raises(ValidationError):
        delete_page(editor, page.id)
    with pytest.raises(ValidationError):
        update_page(editor, page.id, title="other")
    assert preview_delete(editor, "page", page.id).blocked


def test_standalone_page_lifecycle_is_recorded(editor):
    page = create_page(editor, "  Guest Wi-Fi  ", "first")
    assert (page.title, page.item_kind, page.item_id) == ("Guest Wi-Fi", None, None)

    update_page(editor, page.id, title="Wi-Fi", body="second")
    change = item_history(editor, "page", page.id)[0]
    assert {(c.field, c.old, c.new) for c in change.changes} == {
        ("title", "Guest Wi-Fi", "Wi-Fi"),
        ("body", "first", "second"),
    }

    delete_page(editor, page.id)
    assert item_history(editor, "page", page.id)[0].action == "deleted"


@pytest.mark.parametrize("title", ["", "   ", "x" * 201, None])
def test_standalone_page_needs_a_valid_title(editor, title):
    with pytest.raises(ValidationError):
        create_page(editor, title)


def test_viewer_reads_but_cannot_write(editor, viewer):
    page = create_page(editor, "Doc")

    assert get_page(viewer, page.id).title == "Doc"
    for write in (
        lambda: create_page(viewer, "x"),
        lambda: update_page(viewer, page.id, body="x"),
        lambda: delete_page(viewer, page.id),
    ):
        with pytest.raises(PermissionDenied):
            write()


def test_mention_lists_the_page_and_follows_renames(editor, device):
    page = create_page(editor, "Doc", f"See @[device:{device.id}].")
    assert [p.id for p in referenced_by(editor, "device", device.id)] == [page.id]

    update_device(editor, device.id, name="renamed")

    html = render_page(editor, page.id, link)
    assert f'<a href="/device/{device.id}/">renamed</a>' in html
    assert get_page(editor, page.id).body == f"See @[device:{device.id}]."


def test_removing_a_mention_removes_the_reference(editor, device):
    page = create_page(editor, "Doc", f"@[device:{device.id}]")

    update_page(editor, page.id, body="nothing")

    assert referenced_by(editor, "device", device.id) == []
    assert page.id in [p.id for p in list_pages(editor)]


def test_editor_shows_names_and_saving_stores_ids(editor, device):
    page = create_page(editor, "Doc", f"See @[device:{device.id}] now")
    shown = editor_body(editor, page.id)
    assert shown == f"See @[core-sw](device:{device.id}) now"

    update_device(editor, device.id, name="renamed")
    update_page(editor, page.id, body=shown.replace("\n", "\r\n"))

    assert get_page(editor, page.id).body == f"See @[device:{device.id}] now"
    assert len(item_history(editor, "page", page.id)) == 1


def test_mention_in_code_is_plain_text(editor, device):
    body = f"`@[device:{device.id}]`\n\n```\n@[device:{device.id}]\n```\n"
    page = create_page(editor, "Doc", body)

    assert referenced_by(editor, "device", device.id) == []
    assert "<a " not in render_page(editor, page.id, link)
    assert editor_body(editor, page.id) == body


def test_mention_of_a_deleted_item_is_visibly_broken(editor, device):
    port = create_interface(editor, device.id, name="eth0", kind="physical")
    page = create_page(editor, "Doc", f"@[interface:{port.id}]")

    delete_interface(editor, port.id)

    assert 'class="broken-mention"' in render_page(editor, page.id, link)
    assert editor_body(editor, page.id) == f"@[interface:{port.id}]"


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "[x](javascript:alert(1))",
    ],
)
def test_page_content_cannot_inject_markup(editor, body):
    page = create_page(editor, "Doc", body)

    html = render_page(editor, page.id, link)

    assert "<script" not in html
    assert "<img" not in html
    assert 'href="javascript' not in html


def test_item_name_cannot_inject_markup_through_a_mention(editor, site):
    item = create_device(editor, name="<b>x</b>", kind="physical", site_id=site.id)
    page = create_page(editor, "Doc", f"@[device:{item.id}]")

    assert "<b>" not in render_page(editor, page.id, link)


def test_mention_options_find_items_by_name(editor, device):
    create_interface(editor, device.id, name="eth0", kind="physical")

    options = mention_options(editor, "ETH")

    assert [(o.kind, o.label) for o in options] == [("interface", "core-sw eth0")]
    assert options[0].token == f"@[core-sw eth0](interface:{options[0].id})"


def test_pages_carry_tags_and_filter_by_them(editor):
    tagged = create_page(editor, "Tagged")
    create_page(editor, "Other")

    add_tag(editor, "page", tagged.id, "prod")

    assert item_history(editor, "page", tagged.id)[0].changes[0].field == "tags"
    shown = filter_by_tags("page", list_pages(editor), ["prod"])
    assert [p.id for p in shown] == [tagged.id]


def test_migration_gives_existing_items_a_page(editor, device):
    delete_item(editor, Page.objects.get(item_kind="device", item_id=device.id))
    migration = importlib.import_module(
        "netatlas.pages.migrations.0002_item_pages_for_existing_items"
    )
    with allow_tracked_writes():
        Page.objects.all().delete()
        migration.create_item_pages(django_apps, None)

    page = get_item_page(editor, "device", device.id)
    assert page.title == "core-sw"
    assert item_history(editor, "page", page.id)[0].actor == "system"


def test_web_standalone_page_flow(client_with_role):
    client = client_with_role(Role.EDITOR)

    response = client.post("/pages/", {"title": "Wi-Fi", "body": "# Hello"})
    assert response.status_code == 302
    path = response["Location"]
    assert "<h1>Hello</h1>" in client.get(path).content.decode()

    assert client.post(f"{path}edit/", {"title": "Guest", "body": "text"}).status_code == 302
    assert "Guest" in client.get("/pages/").content.decode()

    assert confirm_delete(client, f"{path}delete/")["Location"] == "/pages/"
    assert client.get(path).status_code == 404


def test_web_invalid_page_keeps_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)

    response = client.post("/pages/", {"title": " ", "body": "keep me"})

    assert response.status_code == 400
    assert "keep me" in response.content.decode()


def test_web_item_shows_its_page_and_references(client_with_role):
    client = client_with_role(Role.EDITOR)
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    item = create_device(admin, name="core-sw", kind="physical", site_id=site.id)
    own = get_item_page(admin, "device", item.id)
    update_page(admin, own.id, body="**notes**")
    other = create_page(admin, "Wi-Fi", f"@[device:{item.id}]")

    html = client.get(f"/devices/{item.id}/").content.decode()

    assert "<strong>notes</strong>" in html
    assert f'<a href="/pages/{other.id}/">Wi-Fi</a>' in html
    assert f'href="/pages/{own.id}/edit/"' in html


def test_web_viewer_cannot_edit(client_with_role):
    client = client_with_role(Role.VIEWER)
    admin = authenticate(username="root", password="correct-horse-battery")
    page = create_page(admin, "Doc")

    assert client.get(f"/pages/{page.id}/").status_code == 200
    assert client.get(f"/pages/{page.id}/edit/").status_code == 403
    assert client.post("/pages/", {"title": "x"}).status_code == 403


def test_web_mention_options_escape_names(client_with_role):
    client = client_with_role(Role.EDITOR)
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Home")
    create_device(admin, name='<i>"x', kind="physical", site_id=site.id)

    html = client.get("/pages/mention-options/", {"q": "<i"}).content.decode()

    assert 'role="option"' in html
    assert "<i>" not in html


@pytest.fixture
def api(editor):
    return Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 'tests').secret}")


def test_api_page_flow(api, editor, device):
    created = api.post(
        "/api/pages/",
        {"title": "Doc", "body": f"@[core](device:{device.id})\n"},
        content_type="application/json",
    )
    assert created.status_code == 201
    page = created.json()
    assert page["body"] == f"@[device:{device.id}]\n"

    references = api.get(f"/api/items/device/{device.id}/referenced-by/").json()
    assert [r["id"] for r in references["results"]] == [page["id"]]

    patched = api.patch(
        f"/api/pages/{page['id']}/", {"body": "  text"}, content_type="application/json"
    )
    assert patched.json()["body"] == "  text"
    assert api.delete(f"/api/pages/{page['id']}/").status_code == 204


def test_api_item_page_and_its_limits(api, editor, device):
    page = api.get(f"/api/items/device/{device.id}/page/").json()
    assert (page["title"], page["item_id"]) == ("core-sw", device.id)

    assert api.delete(f"/api/pages/{page['id']}/").status_code == 400
    retitled = api.patch(
        f"/api/pages/{page['id']}/", {"title": "x"}, content_type="application/json"
    )
    assert retitled.status_code == 400
    assert api.get("/api/items/interface/1/page/").status_code == 404


def test_api_mention_options_and_tag_filter(api, editor, device):
    assert api.get("/api/mention-options/", {"q": "core"}).json()[0]["kind"] == "device"
    assert api.get("/api/mention-options/", {"typo": "x"}).status_code == 400

    page = create_page(editor, "Doc")
    add_tag(editor, "page", page.id, "prod")
    results = api.get("/api/pages/", {"tag": "prod"}).json()["results"]
    assert [r["id"] for r in results] == [page.id]
