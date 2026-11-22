import re

from django.contrib.staticfiles import finders


def test_every_page_links_the_stylesheet(client, db):
    html = client.get("/login/").content.decode()
    assert re.search(r'<link rel="stylesheet" href="[^"]*netatlas/app\.css"', html)


def test_stylesheet_loads_nothing_from_outside():
    css = open(finders.find("netatlas/app.css"), encoding="utf-8").read()
    assert "http://" not in css
    assert "https://" not in css
    assert "@import" not in css
    assert "url(" not in css
