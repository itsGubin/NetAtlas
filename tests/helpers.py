"""Shared steps for tests that drive the web UI."""

import re


def confirm_delete(client, path):
    """Open a delete confirmation page and confirm it, as a person would."""
    page = client.get(path).content.decode()
    fingerprint = re.search(r'name="fingerprint" value="([0-9a-f]+)"', page).group(1)
    return client.post(path, {"fingerprint": fingerprint})
