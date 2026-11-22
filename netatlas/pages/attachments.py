"""Files on Pages, stored through Django's storage API.

Uploads are untrusted. A file is stored under a random name and only ever served by
`attachment_response`, which shows it inline only if its bytes are a raster image. Everything
else, including SVG and HTML, is a download, so an upload cannot run script in the app.
"""

import re
from dataclasses import dataclass
from functools import partial
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.dispatch import receiver
from django.http import FileResponse, Http404
from django.utils.translation import gettext as _

from netatlas.accounts.services import require_editor, require_role
from netatlas.history.services import create_item, delete_item
from netatlas.history.signals import items_deleting
from netatlas.pages.models import Attachment, Page

DOWNLOAD_TYPE = "application/octet-stream"
MAX_FILENAME_LENGTH = 255


@dataclass(frozen=True)
class AttachmentSummary:
    id: int
    page_id: int
    filename: str
    content_type: str
    size: int
    is_image: bool
    # What to paste into a Page to show this image. Empty for other files.
    markdown: str


def add_attachment(actor, page_id: int, upload) -> AttachmentSummary:
    require_editor(actor)
    if not Page.objects.filter(pk=page_id).exists():
        raise Http404
    limit = settings.MAX_ATTACHMENT_BYTES
    if upload is None or not 0 < upload.size <= limit:
        message = _("Choose a file of at most %(limit)d MB.") % {"limit": limit // (1024 * 1024)}
        raise ValidationError({"file": [message]})

    head = upload.read(12)
    upload.seek(0)
    stored = default_storage.save(f"attachments/{uuid4().hex}", upload)
    try:
        attachment = create_item(
            actor,
            Attachment,
            page_id=page_id,
            filename=_clean_filename(upload.name),
            content_type=_image_type(head) or DOWNLOAD_TYPE,
            size=upload.size,
            storage_name=stored,
        )
    except Exception:
        default_storage.delete(stored)
        raise
    return _summary(attachment)


def list_attachments(actor, page_id: int) -> list[AttachmentSummary]:
    require_role(actor)
    if not Page.objects.filter(pk=page_id).exists():
        raise Http404
    attachments = Attachment.objects.filter(page_id=page_id)
    summaries = [_summary(attachment) for attachment in attachments]
    return sorted(summaries, key=lambda a: (a.filename.casefold(), a.id))


def get_attachment(actor, attachment_id: int) -> AttachmentSummary:
    require_role(actor)
    return _summary(_find(attachment_id))


def delete_attachment(actor, attachment_id: int) -> None:
    require_editor(actor)
    delete_item(actor, _find(attachment_id))


def attachment_response(actor, attachment_id: int) -> FileResponse:
    """The file itself. Only a verified raster image is shown inline."""
    require_role(actor)
    attachment = _find(attachment_id)
    try:
        file = default_storage.open(attachment.storage_name, "rb")
    except FileNotFoundError as error:
        raise Http404 from error
    response = FileResponse(
        file,
        as_attachment=not attachment.is_image,
        filename=attachment.filename,
        content_type=attachment.content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, no-cache"
    return response


@receiver(items_deleting)
def _delete_files(sender, actor, items, **kwargs):
    """Only once the delete commits, so a rollback or a delete preview keeps the file."""
    for item in items:
        if isinstance(item, Attachment):
            transaction.on_commit(partial(default_storage.delete, item.storage_name), robust=True)


def _find(attachment_id: int) -> Attachment:
    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None:
        raise Http404
    return attachment


def _image_type(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _clean_filename(name) -> str:
    base = re.split(r"[/\\]", name or "")[-1]
    clean = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    if clean in ("", ".", ".."):
        return "attachment"
    if len(clean) <= MAX_FILENAME_LENGTH:
        return clean
    stem, dot, extension = clean.rpartition(".")
    if dot and len(extension) < 20:
        return f"{stem[: MAX_FILENAME_LENGTH - len(extension) - 1]}.{extension}"
    return clean[:MAX_FILENAME_LENGTH]


def _summary(attachment) -> AttachmentSummary:
    alt = re.sub(r"[\[\]\\\r\n]+", " ", attachment.filename).strip()
    return AttachmentSummary(
        id=attachment.pk,
        page_id=attachment.page_id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size=attachment.size,
        is_image=attachment.is_image,
        markdown=f"![{alt}](attachment:{attachment.pk})" if attachment.is_image else "",
    )
