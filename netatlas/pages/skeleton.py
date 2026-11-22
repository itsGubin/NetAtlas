"""The text of a Page Template, and how an Item Page's body is built from it.

A Free-text Section is a line `{{section:Notes}}`. A Live Field of the Page's own item is written
`@{this.ip-addresses}`, and becomes `@{device:12.ip-addresses}` on that item's Page. Building the
body keeps Page storage as plain Markdown (ADR 0004), so Mentions, Live Fields and History work
the same on every Page.
"""

import re

from django.utils.translation import gettext as _

from netatlas.pages.live_fields import is_live_field

DEFAULT_SECTION = "Notes"
DEFAULT_SKELETON = f"{{{{section:{DEFAULT_SECTION}}}}}"
MAX_SECTION_NAME_LENGTH = 80

_SECTION = re.compile(r"^[ \t]*\{\{section:([^{}\n]*)\}\}[ \t]*$", re.MULTILINE)
_THIS = re.compile(r"@\{this\.([a-z-]+)\}")


def section_names(skeleton: str) -> list[str]:
    """The template's Free-text Sections, in order."""
    return [match.strip() for match in _SECTION.findall(skeleton)]


def problems(skeleton: str, kind: str) -> list[str]:
    """Why this text cannot be a template for this kind of item."""
    found = []
    names = section_names(skeleton)
    if any(not name for name in names):
        found.append(_("A Free-text Section needs a name."))
    if any(len(name) > MAX_SECTION_NAME_LENGTH for name in names):
        found.append(
            _("A Free-text Section name has at most %(limit)d characters.")
            % {"limit": MAX_SECTION_NAME_LENGTH}
        )
    if len(set(names)) != len(names):
        found.append(_("Each Free-text Section needs its own name."))
    for field in sorted(set(_THIS.findall(skeleton))):
        if not is_live_field(kind, field):
            found.append(_("Unknown Live Field: %(field)s") % {"field": field})
    return found


def single_section(skeleton: str) -> str | None:
    """The section's name, if the template is nothing but one Free-text Section."""
    match = _SECTION.fullmatch(skeleton.strip())
    return match[1].strip() if match else None


def compose(skeleton: str, sections: dict[str, str], kind: str, item_id: int) -> str:
    """The Page's body. Content the template has no section for stays visible at the end."""

    def own_item(text):
        return _THIS.sub(lambda m: f"@{{{kind}:{item_id}.{m[1]}}}", text)

    # Built in one pass, so text written in a section is never read as template syntax.
    parts = []
    position = 0
    for match in _SECTION.finditer(skeleton):
        parts.append(own_item(skeleton[position : match.start()]))
        parts.append(sections.get(match[1].strip(), ""))
        position = match.end()
    parts.append(own_item(skeleton[position:]))
    body = "".join(parts)

    known = set(section_names(skeleton))
    extra = [
        f"## {name}\n\n{sections[name]}"
        for name in sorted(sections)
        if name not in known and sections[name].strip()
    ]
    if not extra:
        return body
    return "\n\n".join([body.rstrip(), *extra] if body.strip() else extra)
