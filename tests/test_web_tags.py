import pytest
from django.contrib.auth import authenticate

from netatlas.accounts.roles import Role
from netatlas.devices.services import create_device
from netatlas.places.services import create_site
from netatlas.tagging.services import add_tag, create_tag, get_tag, list_tags, tags_of
from tests.helpers import confirm_delete
from tests.test_web_deletion import KINDS, items


def root():
    return authenticate(username="root", password="correct-horse-battery")


def item_id(path):
    return int(path.rstrip("/").rsplit("/", 1)[1])


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/tags/", "/tags/1/"])
def test_anonymous_visitor_is_sent_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={path}"


@pytest.mark.parametrize("kind", KINDS)
def test_every_item_page_adds_shows_and_removes_tags(client_with_role, kind):
    client = client_with_role(Role.EDITOR)
    path, _back = items()[kind]
    number = item_id(path)
    add_url = f"/tagged/{kind}/{number}/"

    assert f'action="{add_url}"' in client.get(path).content.decode()

    response = client.post(add_url, {"name": "prod"})
    assert response.status_code == 302
    assert response["Location"] == path
    tag = tags_of(root(), kind, number)[0]
    page = client.get(path).content.decode()
    assert f'<a href="/tags/{tag.id}/">prod</a>' in page

    response = client.post(f"{add_url}{tag.id}/remove/")
    assert response.status_code == 302
    assert tags_of(root(), kind, number) == []


def test_invalid_tag_shows_the_error_on_the_item_page(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    response = client.post(f"/tagged/site/{site.id}/", {"name": "x" * 51}, follow=True)

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()
    assert list_tags(root()) == []


def test_viewer_sees_tags_without_forms_and_cannot_write(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    tag = add_tag(root(), "site", site.id, "prod")

    for path in (f"/sites/{site.id}/", "/tags/", f"/tags/{tag.id}/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content.decode().count('method="post"') == 1
    assert 'href="/tags/' in client.get(f"/sites/{site.id}/").content.decode()

    assert client.post("/tags/", {"name": "dev"}).status_code == 403
    assert client.post(f"/tags/{tag.id}/", {"name": "x"}).status_code == 403
    assert client.post(f"/tags/{tag.id}/delete/").status_code == 403
    assert client.post(f"/tagged/site/{site.id}/", {"name": "dev"}).status_code == 403
    assert client.post(f"/tagged/site/{site.id}/{tag.id}/remove/").status_code == 403


def test_writer_creates_renames_and_deletes_a_tag(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")

    assert client.post("/tags/", {"name": "prod"}).status_code == 302
    tag = list_tags(root())[0]
    add_tag(root(), "site", site.id, "prod")

    client.post(f"/tags/{tag.id}/", {"name": "Production"})
    assert get_tag(root(), tag.id).name == "Production"

    page = client.get(f"/tags/{tag.id}/delete/").content.decode()
    assert "Home" in page
    assert "loses this Tag" in page

    response = confirm_delete(client, f"/tags/{tag.id}/delete/")
    assert response.status_code == 302
    assert response["Location"] == "/tags/"
    assert list_tags(root()) == []


def test_invalid_tag_form_keeps_the_input(client_with_role):
    client = client_with_role(Role.EDITOR)
    create_tag(root(), "prod")

    response = client.post("/tags/", {"name": "PROD"})

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert 'value="PROD"' in response.content.decode()


def test_tag_page_lists_tagged_items_with_links(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    device = create_device(root(), name="server", kind="physical", site_id=site.id)
    tag = add_tag(root(), "device", device.id, "prod")

    page = client.get(f"/tags/{tag.id}/").content.decode()

    assert f'<a href="/devices/{device.id}/">server</a>' in page
    assert 'data-history-action="created"' in page


@pytest.mark.parametrize(
    "path",
    [
        "/customers/",
        "/sites/",
        "/devices/",
        "/racks/",
        "/cables/",
        "/subnets/",
        "/vlans/",
        "/ip-addresses/",
        "/device-types/",
    ],
)
def test_every_list_offers_a_tag_filter(client_with_role, path):
    client = client_with_role(Role.VIEWER)
    create_tag(root(), "prod")

    page = client.get(path).content.decode()

    assert f'<a href="{path}?tag=prod"' in page


def test_list_filters_by_all_chosen_tags(client_with_role):
    client = client_with_role(Role.VIEWER)
    site = create_site(root(), name="Home")
    one = create_device(root(), name="only-prod", kind="physical", site_id=site.id)
    two = create_device(root(), name="prod-and-dmz", kind="physical", site_id=site.id)
    create_device(root(), name="untagged", kind="physical", site_id=site.id)
    add_tag(root(), "device", one.id, "prod")
    add_tag(root(), "device", two.id, "prod")
    add_tag(root(), "device", two.id, "dmz")

    single = client.get("/devices/?tag=prod").content.decode()
    assert "only-prod" in single and "prod-and-dmz" in single and "untagged" not in single
    assert 'href="/devices/?tag=prod&amp;tag=dmz"' in single
    assert 'aria-current="true"' in single

    both = client.get("/devices/?tag=prod&tag=dmz").content.decode()
    assert "only-prod" not in both and "prod-and-dmz" in both
    assert 'href="/devices/?tag=dmz"' in both


def test_filtering_devices_keeps_all_hypervisor_choices(client_with_role):
    client = client_with_role(Role.EDITOR)
    site = create_site(root(), name="Home")
    host = create_device(root(), name="host", kind="physical", site_id=site.id)
    create_tag(root(), "prod")

    page = client.get("/devices/?tag=prod").content.decode()

    assert f'<option value="{host.id}"' in page


def test_tag_names_in_links_are_escaped(client_with_role):
    client = client_with_role(Role.VIEWER)
    create_tag(root(), "a&b <c>")

    page = client.get("/devices/").content.decode()

    assert "<c>" not in page
    assert 'href="/devices/?tag=a%26b+%3Cc%3E"' in page


def test_navigation_links_to_tags(client_with_role):
    assert 'href="/tags/"' in client_with_role(Role.VIEWER).get("/").content.decode()
