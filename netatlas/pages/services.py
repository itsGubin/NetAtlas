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
from netatlas.devices.models import Device, DeviceType
from netatlas.history.services import create_item, create_system_item, delete_item, update_item
from netatlas.history.signals import item_created, items_deleting
from netatlas.pages import markup, skeleton
from netatlas.pages.live_fields import LIVE_FIELDS, field_label, read_value
from netatlas.pages.models import (
    ITEM_PAGE_MODELS,
    MENTIONABLE_MODELS,
    Attachment,
    Mention,
    Page,
    PageTemplate,
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
    sections: dict[str, str]
    template_id: int | None


@dataclass(frozen=True)
class EditorSection:
    """A Free-text Section as the editor shows it."""

    name: str
    content: str
    # The Page's current template has no such section, so the content sits at the Page's end.
    in_template: bool


@dataclass(frozen=True)
class PageTemplateSummary:
    id: int
    name: str
    item_kind: str
    device_type_id: int | None
    device_type_name: str | None
    is_default: bool
    body: str
    sections: list[str]
    page_count: int


@dataclass(frozen=True)
class RemovedSection:
    """A Free-text Section a template edit would remove, and the Pages with content in it."""

    name: str
    page_count: int


class UnconfirmedSectionRemoval(Exception):
    """A template edit would delete Page content that the person has not confirmed."""

    def __init__(self, removed: list[RemovedSection]):
        super().__init__("Removing these Free-text Sections needs confirmation.")
        self.removed = removed


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


@dataclass(frozen=True)
class BrokenEmbed:
    """An embedded attachment that a delete would break."""

    page_id: int
    page_title: str
    attachment_id: int
    attachment_label: str


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


def update_page(actor, page_id: int, *, title=None, body=None, sections=None) -> PageDetail:
    """Change a Page. An Item Page takes its name from its item, so its title cannot be set.

    An Item Page is written through `sections`. `body` also works while its template is a
    single Free-text Section, because then the two say the same thing.
    """
    require_editor(actor)
    with transaction.atomic():
        page = _find(page_id)
        values = {}
        if title is not None:
            if page.item_id is not None:
                message = _("An Item Page is named after its item.")
                raise ValidationError({"title": [message]})
            values["title"] = _clean_title(title)
        if page.item_id is None:
            if sections is not None:
                message = _("Only an Item Page has Free-text Sections.")
                raise ValidationError({"sections": [message]})
            if body is not None:
                values["body"] = markup.to_stored(body)
        elif body is not None or sections is not None:
            values.update(_item_page_content(page, body, sections))
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

    embedded = Attachment.objects.filter(pk__in=markup.attachments_in(body))
    attachments = {attachment.pk: attachment for attachment in embedded}

    def attachment(attachment_id, text):
        found = attachments.get(attachment_id)
        if found is None:
            return format_html('<mark class="broken-attachment">{}</mark>', _("deleted attachment"))
        url = link("attachment", attachment_id)
        if found.is_image:
            return format_html('<img src="{}" alt="{}">', url, text)
        return format_html('<a href="{}">{}</a>', url, text or found.filename)

    return markup.render(body, mention, live_field, attachment)


def editor_body(actor, page_id: int) -> str:
    """The Page's body with each reference showing its item's current name."""
    require_role(actor)
    return _editor_text(_find(page_id).body)


def editor_sections(actor, page_id: int) -> list[EditorSection]:
    """An Item Page's Free-text Sections: the template's first, then any it no longer has."""
    require_role(actor)
    page = _find(page_id)
    if page.item_id is None:
        return []
    names = skeleton.section_names(_skeleton_of(page))
    extra = sorted(name for name, text in page.sections.items() if name not in names and text)
    return [
        EditorSection(name, _editor_text(page.sections.get(name, "")), name in names)
        for name in [*names, *extra]
    ]


def _editor_text(body: str) -> str:
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


def broken_embeds(deleted: Iterable[tuple[str, int, str]]) -> list[BrokenEmbed]:
    """The embeds on remaining Pages that show the given (kind, id, label) attachments."""
    labels = {item_id: label for kind, item_id, label in deleted if kind == "attachment"}
    if not labels:
        return []
    matching = Q()
    for attachment_id in labels:
        matching |= Q(body__contains=f"attachment:{attachment_id}")
    broken = [
        BrokenEmbed(page.pk, str(page), attachment_id, labels[attachment_id])
        for page in Page.objects.filter(matching)
        for attachment_id in markup.attachments_in(page.body)
        if attachment_id in labels
    ]
    return sorted(broken, key=lambda b: (b.page_id, b.attachment_id))


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
    kind = item.history_kind
    body = skeleton.compose(_template_body(kind, item.pk), {}, kind, item.pk)
    values = {"item_kind": kind, "item_id": item.pk, "body": body}
    if actor is None:
        create_system_item(Page, **values)
    else:
        create_item(actor, Page, **values)


@receiver(items_deleting)
def _delete_item_pages(sender, actor, items, **kwargs):
    for item in items:
        if isinstance(item, PageTemplate) and item.device_type_id is not None:
            # Its Devices go back to the default, whether the override or its type is deleted.
            pages = _pages_of_device_type(item.device_type_id)
            _recompose(actor, pages, _default_body("device"))
        if item.history_kind in ITEM_PAGE_MODELS:
            page = Page.objects.filter(item_kind=item.history_kind, item_id=item.pk).first()
            if page is not None:
                delete_item(actor, page)


def list_page_templates(actor) -> list[PageTemplateSummary]:
    """The default of each kind, then the Device Type overrides, by name."""
    require_role(actor)
    templates = PageTemplate.objects.select_related("device_type")
    summaries = [_template_summary(template) for template in templates]
    return sorted(summaries, key=lambda t: (not t.is_default, t.name.casefold(), t.id))


def get_page_template(actor, template_id: int) -> PageTemplateSummary:
    require_role(actor)
    return _template_summary(_find_template(template_id))


def create_page_template(
    actor, name: str, device_type_id: int, body: str = skeleton.DEFAULT_SKELETON
) -> PageTemplateSummary:
    """Override the Device default for one Device Type. Its Devices' Pages follow at once."""
    require_editor(actor)
    with transaction.atomic():
        device_type = DeviceType.objects.filter(pk=device_type_id).first()
        if device_type is None:
            message = _("Choose an existing Device Type.")
            raise ValidationError({"device_type_id": [message]})
        if PageTemplate.objects.filter(device_type=device_type).exists():
            message = _("This Device Type already has a Page Template.")
            raise ValidationError({"device_type_id": [message]})
        template = create_item(
            actor,
            PageTemplate,
            name=name.strip() if isinstance(name, str) else "",
            item_kind="device",
            device_type=device_type,
            body=_clean_skeleton(body, "device"),
        )
        _recompose(actor, _pages_of_device_type(device_type.pk), template.body)
    return _template_summary(template)


def preview_template_change(actor, template_id: int, body: str) -> list[RemovedSection]:
    """The sections this new text would remove, with how many Pages have content in each."""
    require_editor(actor)
    template = _find_template(template_id)
    clean = _clean_skeleton(body, template.item_kind)
    return _removed_sections(template, _sections_removed_by(template, clean))


def update_page_template(
    actor, template_id: int, *, name=None, body=None, confirmed_removals=None
) -> PageTemplateSummary:
    """Change a template and every Page using it, keeping Free-text Section content (ADR 0005).

    Removing a section with content needs `confirmed_removals`, mapping each such section to
    the number of affected Pages the person was shown. Any other number refuses the change.
    """
    require_editor(actor)
    with transaction.atomic():
        template = PageTemplate.objects.select_for_update().filter(pk=template_id).first()
        if template is None:
            raise Http404
        values = {}
        if name is not None:
            values["name"] = name.strip() if isinstance(name, str) else ""
        removed = set()
        if body is not None:
            values["body"] = _clean_skeleton(body, template.item_kind)
            removed = _sections_removed_by(template, values["body"])
            affected = _removed_sections(template, removed)
            shown = {entry.name: entry.page_count for entry in affected}
            if shown and shown != (confirmed_removals or {}):
                raise UnconfirmedSectionRemoval(affected)
        pages = list(_pages_using(template))
        updated = update_item(actor, template, **values)
        if updated.body != template.body:
            _recompose(actor, pages, updated.body, drop=removed)
    return _template_summary(updated)


def delete_page_template(actor, template_id: int) -> None:
    """Delete a Device Type's override. Each kind always keeps its default."""
    require_editor(actor)
    template = _find_template(template_id)
    if template.device_type_id is None:
        raise ValidationError(_("A default Page Template cannot be deleted."))
    delete_item(actor, template)


def _find_template(template_id: int) -> PageTemplate:
    template = PageTemplate.objects.select_related("device_type").filter(pk=template_id).first()
    if template is None:
        raise Http404
    return template


def _clean_skeleton(body, kind: str) -> str:
    text = markup.to_stored(body if isinstance(body, str) else "")
    found = skeleton.problems(text, kind)
    if found:
        raise ValidationError({"body": found})
    return text


def _template_summary(template) -> PageTemplateSummary:
    device_type = template.device_type
    return PageTemplateSummary(
        id=template.pk,
        name=template.name,
        item_kind=template.item_kind,
        device_type_id=template.device_type_id,
        device_type_name=str(device_type) if device_type else None,
        is_default=device_type is None,
        body=template.body,
        sections=skeleton.section_names(template.body),
        page_count=_pages_using(template).count(),
    )


def _pages_of_device_type(device_type_id: int):
    devices = Device.objects.filter(device_type_id=device_type_id).values("pk")
    return Page.objects.filter(item_kind="device", item_id__in=devices)


def _pages_using(template):
    if template.device_type_id is not None:
        return _pages_of_device_type(template.device_type_id)
    pages = Page.objects.filter(item_kind=template.item_kind)
    if template.item_kind != "device":
        return pages
    overridden = PageTemplate.objects.filter(device_type__isnull=False).values("device_type_id")
    return pages.exclude(
        item_id__in=Device.objects.filter(device_type_id__in=overridden).values("pk")
    )


def _sections_removed_by(template, new_body: str) -> set[str]:
    return set(skeleton.section_names(template.body)) - set(skeleton.section_names(new_body))


def _removed_sections(template, names: set[str]) -> list[RemovedSection]:
    """Only sections that some Page has content in."""
    if not names:
        return []
    counts = dict.fromkeys(names, 0)
    for sections in _pages_using(template).values_list("sections", flat=True):
        for name in names:
            if sections.get(name, "").strip():
                counts[name] += 1
    return [RemovedSection(name, counts[name]) for name in sorted(names) if counts[name]]


def _recompose(actor, pages, template_body: str, drop: Iterable[str] = ()) -> None:
    """Rebuild Pages from a template, through History so removed content stays recoverable."""
    for page in pages:
        sections = {name: text for name, text in page.sections.items() if name not in drop}
        body = skeleton.compose(template_body, sections, page.item_kind, page.item_id)
        _sync_mentions(update_item(actor, page, body=body, sections=sections))


def _default_body(kind: str) -> str:
    template = PageTemplate.objects.filter(item_kind=kind, device_type__isnull=True).first()
    return template.body if template else skeleton.DEFAULT_SKELETON


def _template_of(kind: str, item_id: int):
    if kind == "device":
        override = PageTemplate.objects.filter(
            device_type__in=Device.objects.filter(pk=item_id).values("device_type_id")
        ).first()
        if override:
            return override
    return PageTemplate.objects.filter(item_kind=kind, device_type__isnull=True).first()


def _template_body(kind: str, item_id: int) -> str:
    template = _template_of(kind, item_id)
    return template.body if template else skeleton.DEFAULT_SKELETON


def _skeleton_of(page) -> str:
    return _template_body(page.item_kind, page.item_id)


def _item_page_content(page, body, sections) -> dict:
    """The new `body` and `sections` of an Item Page."""
    if body is not None and sections is not None:
        message = _("Send either the body or the sections of an Item Page.")
        raise ValidationError({"sections": [message]})
    template_body = _skeleton_of(page)
    names = skeleton.section_names(template_body)
    if body is not None:
        only = skeleton.single_section(template_body)
        others = any(text.strip() for name, text in page.sections.items() if name != only)
        if only is None or others:
            message = _("This Page has Free-text Sections. Change them through `sections`.")
            raise ValidationError({"body": [message]})
        sections = {only: body}
    unknown = sorted(set(sections) - set(names) - set(page.sections))
    if unknown:
        message = _("Unknown Free-text Section: %(names)s") % {"names": ", ".join(unknown)}
        raise ValidationError({"sections": [message]})
    merged = {**page.sections, **{k: markup.to_stored(v) for k, v in sections.items()}}
    # Content of a section the template no longer has is gone once it is emptied.
    merged = {k: v for k, v in merged.items() if k in names or v.strip()}
    return {
        "sections": merged,
        "body": skeleton.compose(template_body, merged, page.item_kind, page.item_id),
    }


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
    template = _template_of(page.item_kind, page.item_id) if page.item_id is not None else None
    return PageDetail(
        id=page.pk,
        title=str(page),
        item_kind=page.item_kind or None,
        item_id=page.item_id,
        body=page.body,
        sections=page.sections,
        template_id=template.pk if template else None,
    )
