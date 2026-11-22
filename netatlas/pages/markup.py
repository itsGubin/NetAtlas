"""Page Markdown and its Mention syntax (ADR 0004).

A stored Mention is `@[device:12]`, and a stored Live Field is `@{device:12.ip-addresses}`.
The editor shows `@[core-sw](device:12)` and `@{core-sw: IP Addresses}(device:12.ip-addresses)`
instead, so a person sees current names; saving turns them back, so a rename never changes a
stored Page.
"""

import re
from collections.abc import Callable

from markdown_it import MarkdownIt

from netatlas.pages.live_fields import is_live_field
from netatlas.pages.models import MENTIONABLE_MODELS

_STORED = re.compile(r"@\[([a-z-]+):(\d{1,18})\]")
_EDITOR = re.compile(r"@\[[^\]\n]*\]\(([a-z-]+):(\d{1,18})\)")
_LIVE_STORED = re.compile(r"@\{([a-z-]+):(\d{1,18})\.([a-z-]+)\}")
_LIVE_EDITOR = re.compile(r"@\{[^}\n]*\}\(([a-z-]+):(\d{1,18})\.([a-z-]+)\)")


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


def _live_field_rule(state, silent):
    if state.src[state.pos] != "@":
        return False
    match = _LIVE_STORED.match(state.src, state.pos)
    if match is None or not is_live_field(match[1], match[3]):
        return False
    if not silent:
        token = state.push("live_field", "", 0)
        token.meta = {"kind": match[1], "id": int(match[2]), "field": match[3]}
    state.pos = match.end()
    return True


def _render_mention(_renderer, tokens, index, _options, env):
    meta = tokens[index].meta
    return env["mention"](meta["kind"], meta["id"])


def _render_live_field(_renderer, tokens, index, _options, env):
    meta = tokens[index].meta
    return env["live_field"](meta["kind"], meta["id"], meta["field"])


# Raw HTML stays disabled: Page text is user input and is rendered into the app.
_markdown = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_markdown.inline.ruler.before("link", "mention", _mention_rule)
_markdown.inline.ruler.before("link", "live_field", _live_field_rule)
_markdown.add_render_rule("mention", _render_mention)
_markdown.add_render_rule("live_field", _render_live_field)


def _inline_tokens(body: str, kind: str):
    return (
        child
        for token in _markdown.parse(body)
        if token.type == "inline"
        for child in token.children or []
        if child.type == kind
    )


def mentions_in(body: str) -> set[tuple[str, int]]:
    """The items a body mentions. Text in code spans and code blocks is not a Mention."""
    return {(t.meta["kind"], t.meta["id"]) for t in _inline_tokens(body, "mention")}


def live_fields_in(body: str) -> set[tuple[str, int, str]]:
    """The Live Fields in a body. Text in code spans and code blocks is not a Live Field."""
    return {
        (t.meta["kind"], t.meta["id"], t.meta["field"])
        for t in _inline_tokens(body, "live_field")
    }


def render(
    body: str,
    mention: Callable[[str, int], str],
    live_field: Callable[[str, int, str], str],
) -> str:
    """The body as safe HTML. The callables return the HTML of one reference."""
    return _markdown.render(body, {"mention": mention, "live_field": live_field})


def to_stored(body: str) -> str:
    """The body as it is stored: editor references collapsed, line endings normalised."""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = _EDITOR.sub(
        lambda m: f"@[{m[1]}:{m[2]}]" if m[1] in MENTIONABLE_MODELS else m[0],
        text,
    )
    return _LIVE_EDITOR.sub(
        lambda m: f"@{{{m[1]}:{m[2]}.{m[3]}}}" if is_live_field(m[1], m[3]) else m[0],
        text,
    )


def to_editor(
    body: str,
    label: Callable[[str, int], str | None],
    live_label: Callable[[str, int, str], str | None],
) -> str:
    """The body as the editor shows it. A reference to a deleted item stays as stored."""

    def expand(match):
        if match[1] not in MENTIONABLE_MODELS:
            return match[0]
        name = label(match[1], int(match[2]))
        return match[0] if name is None else editor_token(name, match[1], int(match[2]))

    def expand_live(match):
        if not is_live_field(match[1], match[3]):
            return match[0]
        name = live_label(match[1], int(match[2]), match[3])
        if name is None:
            return match[0]
        return live_field_token(name, match[1], int(match[2]), match[3])

    return _LIVE_STORED.sub(expand_live, _STORED.sub(expand, body))


def live_field_token(label: str, kind: str, item_id: int, field: str) -> str:
    clean = re.sub(r"[{}\r\n]+", " ", label).strip()
    return f"@{{{clean}}}({kind}:{item_id}.{field})"


def editor_token(label: str, kind: str, item_id: int) -> str:
    clean = re.sub(r"[\[\]\r\n]+", " ", label).strip()
    return f"@[{clean}]({kind}:{item_id})"
