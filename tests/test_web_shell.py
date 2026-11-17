def test_home_page_renders_the_app_shell_with_htmx(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="en"' in html
    assert "htmx.min.js" in html
    assert '<nav' in html
    assert "NetAtlas" in html
