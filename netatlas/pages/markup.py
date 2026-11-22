"""Page Markdown and its Mention syntax (ADR 0004).

A stored Mention is `@[device:12]`. The editor shows `@[core-sw](device:12)` instead, so a
person sees the current name; saving turns it back, so a rename never changes a stored Page.
"""

import re
from collections.abc import Callable

from markdown_it import MarkdownIt

from netatlas.pages.models import MENTIONABLE_MODELS

_STORED = re.compile(r"@\[([a-z-]+):(\d{1,18})\]")
_EDITOR = re.compile(r"@\[[^\]\n]*\]\(([a-z-]+):(\d{1,18})\)")


def _mention_rule(state, silent):
    if state.src[state.pos] != "@" or state.linkLevel > 0:
        return False
    match = _STORED.match(state.src, state.pos)
    if match is None or match[1] not in MENTIONABLE_MODELS:
        return False
    if not silent:
        token = state.push("mention", "", 0)
        token.meta = {"kind": match[1], "id": int(match[2])}
    state.pos = match.end()
    return True


def _render_mention(_renderer, tokens, index, _options, env):
    meta = tokens[index].meta
    return env["mention"](meta["kind"], meta["id"])


# Raw HTML stays disabled: Page text is user input and is rendered into the app.
_markdown = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_markdown.inline.ruler.before("link", "mention", _mention_rule)
_markdown.add_render_rule("mention", _render_mention)


def mentions_in(body: str) -> set[tuple[str, int]]:
    """The items a body mentions. Text in code spans and code blocks is not a Mention."""
    return {
        (child.meta["kind"], child.meta["id"])
        for token in _markdown.parse(body)
        if token.type == "inline"
        for child in token.children or []
        if child.type == "mention"
    }


def render(body: str, mention: Callable[[str, int], str]) -> str:
    """The body as safe HTML. `mention` returns the HTML of one Mention."""
    return _markdown.render(body, {"mention": mention})


def to_stored(body: str) -> str:
    """The body as it is stored: editor Mentions collapsed, line endings normalised."""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    return _EDITOR.sub(
        lambda m: f"@[{m[1]}:{m[2]}]" if m[1] in MENTIONABLE_MODELS else m[0],
        text,
    )


def to_editor(body: str, label: Callable[[str, int], str | None]) -> str:
    """The body as the editor shows it. A Mention of a deleted item stays as stored."""

    def expand(match):
        if match[1] not in MENTIONABLE_MODELS:
            return match[0]
        name = label(match[1], int(match[2]))
        return match[0] if name is None else editor_token(name, match[1], int(match[2]))

    return _STORED.sub(expand, body)


def editor_token(label: str, kind: str, item_id: int) -> str:
    clean = re.sub(r"[\[\]\r\n]+", " ", label).strip()
    return f"@[{clean}]({kind}:{item_id})"
