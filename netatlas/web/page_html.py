"""Page content as the web UI shows it."""

from django.urls import reverse
from django.utils.safestring import mark_safe

from netatlas.pages.services import render_page


def page_html(user, page_id: int) -> str:
    """The rendered Page. Markup escapes raw HTML, so the result is safe to embed."""
    html = render_page(user, page_id, lambda kind, item_id: reverse(f"web:{kind}", args=[item_id]))
    return mark_safe(html)  # noqa: S308
