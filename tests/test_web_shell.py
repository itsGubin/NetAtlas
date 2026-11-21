def test_home_page_renders_the_app_shell_with_htmx(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="en"' in html
    assert "htmx.min.js" in html
    assert '<nav' in html
    assert "NetAtlas" in html


def test_pages_are_titled_with_their_name_and_the_product(client, unreachable_database):
    assert "<title>Home · NetAtlas</title>" in client.get("/").content.decode()
    assert "<title>Status · NetAtlas</title>" in client.get("/status/").content.decode()
