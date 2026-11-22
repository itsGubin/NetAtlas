"""Item Pages, Standalone Pages and Mentions (ADR 0004)."""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.dispatch import receiver
from django.http import Http404
from django.utils.html import format_html
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.history.services import create_item, create_system_item, delete_item, update_item
from netatlas.history.signals import item_created, items_deleting
from netatlas.pages import markup
from netatlas.pages.live_fields import LIVE_FIELDS, field_label, read_value
from netatlas.pages.models import (
    ITEM_PAGE_MODELS,
    MENTIONABLE_MODELS,
    Mention,
    Page,
    with_labels,
)

MAX_TITLE_LENGTH = 200
MAX_MENTION_OPTIONS = 10
MAX_LIVE_FIELD_OPTIONS = 20


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


@dataclass(frozen=True)
class LiveFieldOption:
    kind: str
    id: int
    field: str
    label: str
    token: str


@dataclass(frozen=True)
class LiveFieldValue:
    kind: str
    item_id: int
    field: str
    field_label: str
    item_label: str | None
    values: list[str]
    is_broken: bool


@dataclass(frozen=True)
class BrokenLiveField:
    """A Live Field that a delete would break."""

    page_id: int
    page_title: str
    kind: str
    item_id: int
    item_label: str
    field: str
    field_label: str


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
    """The Page as safe HTML, with current values. `link` gives a mentioned item's URL."""
    require_role(actor)
    body = _find(page_id).body
    live_fields = markup.live_fields_in(body)
    items = _items(markup.mentions_in(body) | {(k, i) for k, i, _field in live_fields})

    def mention(kind, item_id):
        item = items.get((kind, item_id))
        if item is None:
            return format_html('<mark class="broken-mention">{}</mark>', _("deleted item"))
        return format_html('<a href="{}">{}</a>', link(kind, item_id), str(item))

    def live_field(kind, item_id, field):
        item = items.get((kind, item_id))
        if item is None:
            message = _("broken Live Field: %(field)s of a deleted item") % {
                "field": field_label(kind, field)
            }
            return format_html('<mark class="broken-live-field">{}</mark>', message)
        values = read_value(item, kind, field)
        return format_html(
            '<span class="live-field">{}</span>', ", ".join(values) if values else "—"
        )

    return markup.render(body, mention, live_field)


def editor_body(actor, page_id: int) -> str:
    """The Page's body with each reference showing its item's current name."""
    require_role(actor)
    body = _find(page_id).body
    live_fields = markup.live_fields_in(body)
    labels = _labels(markup.mentions_in(body) | {(k, i) for k, i, _field in live_fields})

    def live_label(kind, item_id, field):
        if (kind, item_id, field) not in live_fields or (kind, item_id) not in labels:
            return None
        return f"{labels[(kind, item_id)]}: {field_label(kind, field)}"

    return markup.to_editor(body, lambda kind, item_id: labels.get((kind, item_id)), live_label)


def live_field_values(actor, page_id: int) -> list[LiveFieldValue]:
    """Every Live Field in the Page with its current value."""
    require_role(actor)
    live_fields = markup.live_fields_in(_find(page_id).body)
    items = _items({(kind, item_id) for kind, item_id, _field in live_fields})
    values = []
    for kind, item_id, field in sorted(live_fields):
        item = items.get((kind, item_id))
        values.append(
            LiveFieldValue(
                kind=kind,
                item_id=item_id,
                field=field,
                field_label=field_label(kind, field),
                item_label=str(item) if item else None,
                values=read_value(item, kind, field) if item else [],
                is_broken=item is None,
            )
        )
    return values


def live_field_options(actor, query: str) -> list[LiveFieldOption]:
    """Item fields matching every word of the query, for the Live Field picker."""
    require_role(actor)
    words = query.casefold().split()
    found = []
    for kind, fields in LIVE_FIELDS.items():
        for item in with_labels(kind):
            name = str(item)
            for field in fields:
                label = f"{name}: {field_label(kind, field)}"
                if all(word in label.casefold() for word in words):
                    token = markup.live_field_token(label, kind, item.pk, field)
                    found.append(LiveFieldOption(kind, item.pk, field, label, token))
    found.sort(key=lambda option: (option.label.casefold(), option.kind, option.id))
    return found[:MAX_LIVE_FIELD_OPTIONS]


def broken_live_fields(deleted: Iterable[tuple[str, int, str]]) -> list[BrokenLiveField]:
    """The Live Fields on remaining Pages that point at the given (kind, id, label) items."""
    labels = {(kind, item_id): label for kind, item_id, label in deleted if kind in LIVE_FIELDS}
    if not labels:
        return []
    matching = Q()
    for kind, item_id in labels:
        matching |= Q(body__contains=f"@{{{kind}:{item_id}.")
    broken = [
        BrokenLiveField(
            page_id=page.pk,
            page_title=str(page),
            kind=kind,
            item_id=item_id,
            item_label=labels[(kind, item_id)],
            field=field,
            field_label=field_label(kind, field),
        )
        for page in Page.objects.filter(matching)
        for kind, item_id, field in markup.live_fields_in(page.body)
        if (kind, item_id) in labels
    ]
    return sorted(broken, key=lambda b: (b.page_id, b.kind, b.item_id, b.field))


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


def _items(references: Iterable[tuple[str, int]]) -> dict[tuple[str, int], object]:
    """Each referenced item that still exists."""
    ids = defaultdict(set)
    for kind, item_id in references:
        ids[kind].add(item_id)
    return {
        (kind, item.pk): item
        for kind, wanted in ids.items()
        for item in with_labels(kind).filter(pk__in=wanted)
    }


def _labels(references: Iterable[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """The current name of each item that still exists."""
    return {key: str(item) for key, item in _items(references).items()}


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
