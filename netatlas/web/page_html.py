"""Page content as the web UI shows it."""

from django.urls import reverse
from django.utils.safestring import mark_safe

from netatlas.pages.services import preview_page, render_page


def page_html(user, page_id: int) -> str:
    """The rendered Page. Markup escapes raw HTML, so the result is safe to embed."""
    html = render_page(user, page_id, _item_url)
    return mark_safe(html)  # noqa: S308


def page_preview_html(user, page_id=None, body=None, sections=None) -> str:
    """The Page as it would look once saved. Markup escapes raw HTML here too."""
    html = preview_page(user, _item_url, page_id=page_id, body=body, sections=sections)
    return mark_safe(html)  # noqa: S308


def _item_url(kind: str, item_id: int) -> str:
    return reverse(f"web:{kind}", args=[item_id])
