"""Item Pages, Standalone Pages and Mentions (ADR 0004)."""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.dispatch import receiver
from django.http import Http404
from django.utils.html import format_html
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.history.services import create_item, create_system_item, delete_item, update_item
from netatlas.history.signals import item_created, items_deleting
from netatlas.pages import markup
from netatlas.pages.models import (
    ITEM_PAGE_MODELS,
    MENTIONABLE_MODELS,
    Mention,
    Page,
    with_labels,
)

MAX_TITLE_LENGTH = 200
MAX_MENTION_OPTIONS = 10


@dataclass(frozen=True)
class PageSummary:
    id: int
    title: str
    item_kind: str | None
    item_id: int | None


@dataclass(frozen=True)
class PageDetail(PageSummary):
    body: str


@dataclass(frozen=True)
class MentionOption:
    kind: str
    id: int
    label: str
    token: str


def list_pages(actor) -> list[PageSummary]:
    """Every Page, by name."""
    require_role(actor)
    summaries = [_summary(page) for page in Page.objects.all()]
    return sorted(summaries, key=lambda page: (page.title.casefold(), page.id))


def get_page(actor, page_id: int) -> PageDetail:
    require_role(actor)
    return _detail(_find(page_id))


def get_item_page(actor, kind: str, item_id: int) -> PageDetail:
    require_role(actor)
    if kind not in ITEM_PAGE_MODELS:
        raise Http404
    page = Page.objects.filter(item_kind=kind, item_id=item_id).first()
    if page is None:
        raise Http404
    return _detail(page)


def has_item_page(kind: str) -> bool:
    return kind in ITEM_PAGE_MODELS


def create_page(actor, title: str, body: str = "") -> PageDetail:
    """Create a Standalone Page."""
    require_editor(actor)
    with transaction.atomic():
        page = create_item(actor, Page, title=_clean_title(title), body=markup.to_stored(body))
        _sync_mentions(page)
    return _detail(page)


def update_page(actor, page_id: int, *, title=None, body=None) -> PageDetail:
    """Change a Page. An Item Page takes its name from its item, so its title cannot be set."""
    require_editor(actor)
    with transaction.atomic():
        page = _find(page_id)
        values = {}
        if title is not None:
            if page.item_id is not None:
                message = _("An Item Page is named after its item.")
                raise ValidationError({"title": [message]})
            values["title"] = _clean_title(title)
        if body is not None:
            values["body"] = markup.to_stored(body)
        page = update_item(actor, page, **values)
        _sync_mentions(page)
    return _detail(page)


def delete_page(actor, page_id: int) -> None:
    """Delete a Standalone Page. An Item Page exists as long as its item does."""
    require_editor(actor)
    page = _find(page_id)
    if page.item_id is not None:
        raise ValidationError(_("An Item Page is deleted together with its item."))
    delete_item(actor, page)


def render_page(actor, page_id: int, link: Callable[[str, int], str]) -> str:
    """The Page as safe HTML. `link` gives the URL of a mentioned item."""
    require_role(actor)
    body = _find(page_id).body
    labels = _labels(markup.mentions_in(body))

    def mention(kind, item_id):
        label = labels.get((kind, item_id))
        if label is None:
            return format_html('<mark class="broken-mention">{}</mark>', _("deleted item"))
        return format_html('<a href="{}">{}</a>', link(kind, item_id), label)

    return markup.render(body, mention)


def editor_body(actor, page_id: int) -> str:
    """The Page's body with each Mention showing its item's current name."""
    require_role(actor)
    body = _find(page_id).body
    labels = _labels(markup.mentions_in(body))
    return markup.to_editor(body, lambda kind, item_id: labels.get((kind, item_id)))


def referenced_by(actor, kind: str, item_id: int) -> list[PageSummary]:
    """Every Page that mentions the item, by name."""
    require_role(actor)
    if kind not in MENTIONABLE_MODELS:
        raise Http404
    if not MENTIONABLE_MODELS[kind].objects.filter(pk=item_id).exists():
        raise Http404
    pages = Page.objects.filter(mentions__item_kind=kind, mentions__item_id=item_id)
    summaries = [_summary(page) for page in pages]
    return sorted(summaries, key=lambda page: (page.title.casefold(), page.id))


def mention_options(actor, query: str) -> list[MentionOption]:
    """Items whose name contains the query, for the `@` picker."""
    require_role(actor)
    needle = query.strip().casefold()
    found = []
    for kind in MENTIONABLE_MODELS:
        for item in with_labels(kind):
            label = str(item)
            if needle in label.casefold():
                found.append(
                    MentionOption(kind, item.pk, label, markup.editor_token(label, kind, item.pk))
                )
    found.sort(key=lambda option: (option.label.casefold(), option.kind, option.id))
    return found[:MAX_MENTION_OPTIONS]


@receiver(item_created)
def _create_item_page(sender, actor, item, **kwargs):
    if item.history_kind not in ITEM_PAGE_MODELS:
        return
    values = {"item_kind": item.history_kind, "item_id": item.pk}
    if actor is None:
        create_system_item(Page, **values)
    else:
        create_item(actor, Page, **values)


@receiver(items_deleting)
def _delete_item_pages(sender, actor, items, **kwargs):
    for item in items:
        if item.history_kind in ITEM_PAGE_MODELS:
            page = Page.objects.filter(item_kind=item.history_kind, item_id=item.pk).first()
            if page is not None:
                delete_item(actor, page)


def _sync_mentions(page) -> None:
    wanted = markup.mentions_in(page.body)
    current = set(page.mentions.values_list("item_kind", "item_id"))
    for kind, item_id in current - wanted:
        page.mentions.filter(item_kind=kind, item_id=item_id).delete()
    Mention.objects.bulk_create(
        Mention(page=page, item_kind=kind, item_id=item_id) for kind, item_id in wanted - current
    )


def _labels(references: Iterable[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """The current name of each item that still exists."""
    ids = defaultdict(set)
    for kind, item_id in references:
        ids[kind].add(item_id)
    return {
        (kind, item.pk): str(item)
        for kind, wanted in ids.items()
        for item in with_labels(kind).filter(pk__in=wanted)
    }


def _find(page_id: int) -> Page:
    page = Page.objects.filter(pk=page_id).first()
    if page is None:
        raise Http404
    return page


def _clean_title(title) -> str:
    clean = title.strip() if isinstance(title, str) else ""
    if not clean:
        raise ValidationError({"title": [_("Enter a title.")]})
    if len(clean) > MAX_TITLE_LENGTH:
        message = _("A title has at most %(limit)d characters.") % {"limit": MAX_TITLE_LENGTH}
        raise ValidationError({"title": [message]})
    return clean


def _summary(page) -> PageSummary:
    return PageSummary(
        id=page.pk,
        title=str(page),
        item_kind=page.item_kind or None,
        item_id=page.item_id,
    )


def _detail(page) -> PageDetail:
    return PageDetail(
        id=page.pk,
        title=str(page),
        item_kind=page.item_kind or None,
        item_id=page.item_id,
        body=page.body,
    )
