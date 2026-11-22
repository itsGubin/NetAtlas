import re

import pytest
from django.contrib.staticfiles import finders


def test_every_page_links_the_stylesheet_and_shell(client, db):
    html = client.get("/login/").content.decode()
    assert re.search(r'<link rel="stylesheet" href="[^"]*netatlas/app\.css"', html)
    assert re.search(r'<script src="[^"]*netatlas/shell\.js"></script>', html)


def test_theme_switch_is_on_the_login_page_and_hidden_without_javascript(client, db):
    html = client.get("/login/").content.decode()
    button = re.search(r'<button type="button" id="theme-toggle"[^>]*>', html).group(0)
    assert " hidden" in button
    assert "aria-label=" in button


@pytest.mark.parametrize("name", ["netatlas/app.css", "netatlas/shell.js"])
def test_shell_loads_nothing_from_outside(name):
    text = open(finders.find(name), encoding="utf-8").read()
    assert "http://" not in text
    assert "https://" not in text
    assert "@import" not in text
    assert "url(" not in text
    assert "fetch(" not in text
