#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""markdown ↔ Yjs bridge for the collaborative editor.

We hold the authoritative Y.Doc server-side (see `business/collab.py`)
and clients speak Yjs updates over the wire. The editor is CodeMirror
bound to a `Y.Text` (`y-codemirror.next`): the document IS its markdown
source, character for character. So the conversions at the storage
boundary are plain text operations:

  * `markdown_to_ydoc_update(md)`  → bytes
      Called ONCE per document when its `collab_doc` row is being
      seeded from the source column (`notes.note_content` etc.). The
      markdown is cleaned of the legacy shapes we know about and put
      into the Text of a fresh Y.Doc.

  * `append_markdown_to_ydoc_update(state, md)` → bytes
      Called when the SERVER needs to add content to a document that
      has already been seeded (alert escalation appending a mention
      chip to a case summary, see `collab/sync.py`). Once a
      `collab_doc` row exists the Y.Doc is authoritative and
      `ensure_snapshot` ignores the source column, so appending to
      the column alone is silently discarded by the next flush.

  * `replace_markdown_in_ydoc_update(state, md)` → bytes
      Called by server-side writers that rewrite a whole document (MCP
      tools, the REST update of a note being edited). Only the span
      that differs is replaced, so concurrent typing elsewhere survives.

  * `ydoc_update_to_markdown(update)` → str
      Called on flush (last-client-disconnect + periodic tick). Returns
      the Text, written back to the source column.

Documents seeded before the CodeMirror editor hold a ProseMirror-shaped
XmlFragment instead (what TipTap's `y-prosemirror` binding produced).
`ydoc_update_to_markdown` still renders those, which is how
`ensure_snapshot` carries their unflushed edits over when it re-seeds
them as text. Nothing writes that shape any more.

pycrdt indexes a Text in UTF-8 bytes, not characters: every positional
edit converts its offsets with `_utf8_offset`.
"""

from __future__ import annotations

import json
import logging
import re
from itertools import groupby

from pycrdt import Doc, Text, XmlElement, XmlFragment, XmlText

from app.iris_engine.collab.mentions import MENTION_DEFAULT_KIND_BY_NODE
from app.iris_engine.collab.mentions import MENTION_KINDS
from app.iris_engine.collab.mentions import MENTION_NODE_NAMES
from app.iris_engine.collab.mentions import build_mention_span


logger = logging.getLogger(__name__)


# The Y.Text field name the CodeMirror binding edits. The frontend
# (`MarkDownEditor.svelte`) and this module MUST agree on this key — a
# mismatch produces an empty editor with no obvious error.
_MARKDOWN_FIELD = 'markdown'

# The XmlFragment field y-prosemirror used: documents seeded before the
# CodeMirror editor. Read only, see `ydoc_update_to_markdown`.
_PROSEMIRROR_FIELD = 'prosemirror'


# ---------------------------------------------------------------------------
# markdown → Y.Doc
# ---------------------------------------------------------------------------

def _clean_markdown(md: str | None) -> str:
    """Clean `md` of the legacy shapes we know about.

    Applied to everything that enters the Y.Doc (seed, append, replace)
    so the document is the same whichever way the markdown came in.

    `_rewrite_legacy_case_urls` rewrites old IRIS v2.4.29 URLs
    (`/case/iocs?cid=X&ioc_id=Y`, `/case?cid=X`, …) to the SvelteKit
    path form; without it the column keeps the old URLs forever after
    the first collab open, since the Y.Doc becomes the source of truth.
    `_strip_fontawesome_tags` runs after it, in the same order as the
    frontend's `normalizeLegacyContent`.

    `_unescape_legacy_markdown_links` and `_rewrite_datastore_urls` lead
    the chain for the same reason: an image written as
    `![x](/datastore/file/view/1?cid=2 =100%x40%)` is not a valid
    markdown image target and would stay literal text.

    `_repair_flushed_mark_markup` comes first of all: it turns the
    `\\<link …>` pseudo-XML an older renderer flushed back into the
    markdown links the other passes then rewrite.

    Some legacy imports concatenated an entire GFM table onto a single
    physical line; `_unflatten_pipe_tables` puts them back on several.
    """
    md = (md or '').replace('\r\n', '\n').replace('\r', '\n')
    cleaned = _strip_fontawesome_tags(
        _rewrite_legacy_case_urls(
            _rewrite_datastore_urls(
                _unescape_legacy_markdown_links(_repair_flushed_mark_markup(md))
            )
        )
    )
    return _unflatten_pipe_tables(cleaned)


def _load_doc(state: bytes | None) -> tuple[Doc, Text]:
    """A Y.Doc holding `state`, and its markdown Text.

    A legacy document (its content in the ProseMirror tree, see the
    module docstring) is converted to text first, so a server write
    lands next to its content instead of in an otherwise empty Text
    that would hide it.
    """
    doc = Doc()
    text = Text()
    doc[_MARKDOWN_FIELD] = text
    frag = XmlFragment()
    doc[_PROSEMIRROR_FIELD] = frag
    if state:
        doc.apply_update(bytes(state))
    if not str(text) and len(frag.children):
        legacy_md = _render_fragment(frag)
        with doc.transaction():
            del frag.children[0:len(frag.children)]
            if legacy_md:
                text += legacy_md
    return doc, text


def _utf8_offset(s: str, index: int) -> int:
    """The pycrdt Text offset of character `index` of `s` (UTF-8 bytes)."""
    return len(s[:index].encode('utf-8'))


def markdown_to_ydoc_update(md: str) -> bytes:
    """Build a fresh Y.Doc from `md` and return its update bytes.

    Empty input is legal — we return the update for an empty doc so
    the client's `sync-init` handler can still apply it cleanly.
    """
    doc, text = _load_doc(None)
    cleaned = _clean_markdown(md)
    if cleaned:
        text += cleaned
    return doc.get_update()


def _append_separator(existing: str) -> str:
    """What goes between `existing` and appended markdown: a blank line."""
    if not existing or existing.endswith('\n\n'):
        return ''
    if existing.endswith('\n'):
        return '\n'
    return '\n\n'


def append_markdown_to_ydoc_update(state: bytes | None, md: str) -> bytes:
    """Append `md` to the end of the Y.Doc encoded by `state`.

    Returns the FULL new state (not a delta), so the result drops
    straight into `collab_doc.y_state` alongside what `ensure_snapshot`
    writes there.

    `state` may be `None` or empty — the result is then
    content-equivalent to `markdown_to_ydoc_update(md)`. Note that the
    two will never be byte-equal: Yjs stamps a random client id into
    every update.

    The appended markdown lands AFTER everything already in the
    document, separated by a blank line so it starts a block of its
    own. That's the only position a server-side writer can safely pick
    — it has no cursor and no idea what a concurrently-connected human
    is editing, and an append at the tail is the least likely to
    collide with them once the CRDT merges.
    """
    doc, text = _load_doc(state)
    cleaned = _clean_markdown(md)
    if cleaned:
        existing = str(text)
        text += _append_separator(existing) + cleaned
    return doc.get_update()


def replace_markdown_in_ydoc_update(state: bytes | None, md: str) -> bytes:
    """Replace the whole content of the Y.Doc encoded by `state` with `md`.

    Returns the FULL new state, like `append_markdown_to_ydoc_update`.

    The edit is made *inside* the CRDT rather than starting from a fresh
    Doc: a fresh Doc has no knowledge of the stored items, so merging it
    with a client's copy would add the new content next to the old
    instead of replacing it. And only the span between the common prefix
    and the common suffix is replaced, so what a connected client typed
    concurrently outside that span survives the merge.
    """
    doc, text = _load_doc(state)
    old = str(text)
    new = _clean_markdown(md)
    if old == new:
        return doc.get_update()

    prefix = 0
    limit = min(len(old), len(new))
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < limit - prefix
           and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]):
        suffix += 1

    start = _utf8_offset(old, prefix)
    old_end = _utf8_offset(old, len(old) - suffix)
    inserted = new[prefix:len(new) - suffix]
    with doc.transaction():
        if old_end > start:
            del text[start:old_end]
        if inserted:
            text.insert(start, inserted)
    return doc.get_update()


_LEGACY_ESCAPED_LINK_RE = re.compile(r'(!?)\\\[([^\]]*)\\\]\(')
_LEGACY_BACKTICKED_ALT_RE = re.compile(r'^`([^`]*)`$')
_LEGACY_IMAGE_SIZE_RE = re.compile(r'(!\[[^\]]*\]\()([^)\s]+)\s+=\d+%?x\d+%?(\))')


def _unescape_legacy_markdown_links(md: str) -> str:
    """Repair the legacy image/link shapes CommonMark can't parse.

    Mirrors `unescapeLegacyMarkdownLinks` in
    `iris-frontend/src/lib/components/common/MarkDown/legacy-content.ts`
    — same byte-for-byte behaviour pact as `_rewrite_legacy_case_urls`.

      * `!\\[alt\\](url)` → `![alt](url)`: IRIS v2 stored some images and
        links with backslash-escaped brackets. This is also the shape
        `_escape_markdown` flushes back to the source column when a
        broken image was seeded as literal text, so un-escaping it lets
        a re-seed recover those documents.
      * ``![`alt`](url)`` → `![alt](url)`: cosmetic backticks around the
        alt text, dropped by the editor anyway.
      * `![alt](url =60%x40%)` → `![alt](url)`: the markdown-it-imsize
        size suffix the v2 editor wrote. It breaks the link destination,
        so the whole image degrades to text. Width is carried by the
        editor's resize handle from here on.
    """
    if '](' not in md:
        return md

    def _unescape(match: re.Match) -> str:
        inner = _LEGACY_BACKTICKED_ALT_RE.sub(r'\1', match.group(2))
        return f'{match.group(1)}[{inner}]('

    out = _LEGACY_ESCAPED_LINK_RE.sub(_unescape, md)
    return _LEGACY_IMAGE_SIZE_RE.sub(r'\1\2\3', out)


# The marks TipTap's StarterKit can put on text in the editor.
_FLUSHED_MARK_TAGS = 'bold|italic|strike|code|link|underline'
# One innermost mark tag, as `str(XmlText)` writes it and
# `_escape_markdown` then escaped it: `\<bold>x\</bold>`,
# `\<link rel="…" href="…" title="…">x\</link>`. The body may not hold
# another mark tag, so nested marks are peeled one level per pass.
_FLUSHED_MARK_RE = re.compile(
    rf'\\?<({_FLUSHED_MARK_TAGS})((?:\s+[\w-]+="[^"]*")*)>'
    rf'((?:(?!\\?</?(?:{_FLUSHED_MARK_TAGS})\b).)*?)'
    r'\\?</\1>'
)
_FLUSHED_MARK_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_MARKDOWN_ESCAPE_RE = re.compile(r'\\([\\`*_\[\]<])')
_FLUSHED_EMPHASIS_MARKERS = {'bold': '**', 'italic': '*', 'strike': '~~', 'underline': ''}


def _repair_flushed_mark_markup(md: str) -> str:
    """Turn mark pseudo-XML flushed to the source column back into markdown.

    Mirrors `repairFlushedMarkMarkup` in `legacy-content.ts`.

    Until marks were read from Yjs formatting, `ydoc_update_to_markdown`
    rendered every bold, italic or link typed in the editor through
    `str(XmlText)`, which pycrdt formats as `<bold>x</bold>` /
    `<link href="…">x</link>`, and escaped that as literal text. A
    re-seed then froze it into the Y.Doc, where the editor shows the
    markup itself. Links typed over a datastore file chip lost their
    text on the way; their `title` (the file name) is used as the label.
    """
    if '</' not in md:
        return md

    def _repair(match: re.Match) -> str:
        tag, raw_attrs, body = match.groups()
        if tag == 'code':
            code = _MARKDOWN_ESCAPE_RE.sub(r'\1', body)
            return f'`{code}`'
        if tag != 'link':
            marker = _FLUSHED_EMPHASIS_MARKERS[tag]
            return _emphasize(body, [marker] if marker else [])
        attrs = {name: _MARKDOWN_ESCAPE_RE.sub(r'\1', value)
                 for name, value in _FLUSHED_MARK_ATTR_RE.findall(raw_attrs)}
        href = attrs.get('href', '')
        title = attrs.get('title')
        if title == 'null':
            title = None
        label = body if body.strip() else _escape_markdown(title or href)
        return _markdown_link(label, href, title)

    previous = None
    while previous != md:
        previous, md = md, _FLUSHED_MARK_RE.sub(_repair, md)
    return md


_LEGACY_DATASTORE_URL_RE = re.compile(r'/datastore/file/view/(\d+)(\?[^)\s"\']*)?')


def _rewrite_datastore_urls(md: str) -> str:
    """Rewrite `/datastore/file/view/{id}?cid={X}` to the v2 endpoint.

    Mirrors `rewriteDatastoreUrls` in `legacy-content.ts`. The target is
    `/api/v2/cases/{X}/datastore/files/{id}`, which is also the shape the
    frontend's `authenticate-datastore-images` recognises to fetch the
    file with the user's token. A URL without a `cid` is left alone —
    there is no way to tell which case it belongs to.
    """
    if '/datastore/file/view/' not in md:
        return md

    def _rewrite(match: re.Match) -> str:
        query = match.group(2)
        if not query:
            return match.group(0)
        cid_match = _LEGACY_CID_RE.search(query)
        if not cid_match:
            return match.group(0)
        return f'/api/v2/cases/{cid_match.group(1)}/datastore/files/{match.group(1)}'

    return _LEGACY_DATASTORE_URL_RE.sub(_rewrite, md)


_LEGACY_CASE_SUBPATH_ID_PARAM = {
    'iocs': 'ioc_id',
    'assets': 'asset_id',
    'tasks': 'id',           # v2.4 tasks used `id=`, not `task_id=`
    'notes': 'note_id',
    'evidences': 'evidence_id',
}

_LEGACY_CASE_SUBPATH_RE = re.compile(
    r'/case/(iocs|assets|tasks|notes|evidences|timeline)(\?[^)\s"\'<>]*)'
)
_LEGACY_CASE_BARE_RE = re.compile(r'/case(\?[^)\s"\'<>]*)')
_LEGACY_CID_RE = re.compile(r'[?&]cid=(\d+)')


def _rewrite_legacy_case_urls(md: str) -> str:
    """Rewrite IRIS v2.4.29 flat-query case URLs to the SvelteKit v2 tree.

    Mirrors `iris-frontend/src/lib/components/common/MarkDown/legacy-content.ts`
    — kept intentionally byte-identical in behaviour so a document rewritten
    server-side matches what the frontend normalizer would produce. Runs
    before the tokens are seeded into the Y.Doc so old links don't get
    locked into collaborative state.
    """
    if '/case' not in md or 'cid=' not in md:
        return md

    def _subpath(match: re.Match) -> str:
        section = match.group(1)
        query = match.group(2)
        cid_match = _LEGACY_CID_RE.search(query)
        if not cid_match:
            return match.group(0)
        cid = cid_match.group(1)
        if section == 'timeline':
            return f'/case/{cid}/timeline'
        id_param = _LEGACY_CASE_SUBPATH_ID_PARAM.get(section)
        if not id_param:
            return match.group(0)
        id_match = re.search(rf'[?&]{id_param}=(\d+)', query)
        if not id_match:
            return f'/case/{cid}/{section}'
        return f'/case/{cid}/{section}/{id_match.group(1)}'

    def _bare(match: re.Match) -> str:
        cid_match = _LEGACY_CID_RE.search(match.group(1))
        if not cid_match:
            return match.group(0)
        return f'/case/{cid_match.group(1)}'

    out = _LEGACY_CASE_SUBPATH_RE.sub(_subpath, md)
    return _LEGACY_CASE_BARE_RE.sub(_bare, out)


# Self-empty FontAwesome `<i>` tags. Mirrors the regex in
# `stripFontAwesomeTags` (legacy-content.ts) character for character;
# the only deviation is Python's `\w` being Unicode-aware where JS's is
# ASCII-only, which cannot change the outcome here because the
# following `[^"']*` already accepts everything `\w` would add.
_FONTAWESOME_TAG_RE = re.compile(
    r'<i\b[^>]*\bclass=["\'][^"\']*\bfa[-\w]*[^"\']*["\'][^>]*>\s*</i>',
    re.IGNORECASE,
)


def _strip_fontawesome_tags(md: str) -> str:
    """Drop `<i class="fa-…"></i>` icon tags from legacy content.

    Mirrors `stripFontAwesomeTags` in
    `iris-frontend/src/lib/components/common/MarkDown/legacy-content.ts`
    — same deliberate byte-for-byte behaviour pact as
    `_rewrite_legacy_case_urls` above.

    IRIS v2 embedded FontAwesome markup inside link text (case
    descriptions written by alert escalation, datastore file links, …).
    This frontend doesn't ship FontAwesome, so leaving them in surfaces
    the raw tag as literal text in the editor. They're always self-empty, so stripping them
    loses nothing: `[<i class='fa-solid fa-bell'></i> #106](…)` reads
    as `[ #106](…)`.

    Only the empty form is matched — an `<i>` with real content keeps
    it rather than having it swallowed.
    """
    if '<i' not in md and '<I' not in md:
        return md
    return _FONTAWESOME_TAG_RE.sub('', md)


def _unflatten_pipe_tables(md: str) -> str:
    """Repair single-line GFM pipe tables produced by legacy exporters.

    A well-formed table is `header | separator | row+`, each on its own
    line. When an import concatenates them onto a single line — `| A | B
    | |---|---| | 1 | 2 |` — a markdown parser can't recognise it and drops
    through to paragraph parsing. Users see pipes and dashes as literal
    text.

    Detection: a line that contains
      * a `| --- | --- | ... |` separator segment, AND
      * multiple `| ... | ... | ... |` groups
    is almost certainly a flattened table. We split on ` | | ` (the
    boundary the concatenation left between adjacent rows) and re-insert
    line breaks so the standard GFM table rule can pick it back up.

    Runs strictly per-line, so paragraphs and non-flat tables are
    untouched. False positives are unlikely — inline pipes in normal
    prose don't produce a `| --- | --- |` separator segment.
    """
    if '|' not in md or '---' not in md:
        return md

    separator_re = re.compile(r'\|(\s*:?-{2,}:?\s*\|)+')
    # A row is `| cell | cell | ... |` with no internal newlines.
    # We split on ` | | ` — the collapsed-out newline between rows —
    # which reliably delimits rows in the pathological single-line
    # shape without matching legitimate mid-row pipe pairs.
    row_boundary_re = re.compile(r'\|\s*\|')

    out_lines: list[str] = []
    for line in md.split('\n'):
        if separator_re.search(line) and row_boundary_re.search(line):
            # Restore the newlines between adjacent `|...|` rows.
            # `.strip()` on each piece removes the extra spaces the
            # collapse left behind so the resulting rows look natural
            # in the source column after a round-trip.
            pieces = [p.strip() for p in row_boundary_re.split(line)]
            # Each interior piece lost a leading `|` (the row-start)
            # and a trailing `|` (the row-end) to the split. Re-add
            # them so we emit valid GFM rows.
            fixed: list[str] = []
            for idx, p in enumerate(pieces):
                if not p:
                    continue
                if idx > 0 and not p.startswith('|'):
                    p = '| ' + p
                if idx < len(pieces) - 1 and not p.endswith('|'):
                    p = p + ' |'
                fixed.append(p)
            # Multi-line tables need a blank line separating them from
            # the surrounding text — otherwise a markdown parser fuses the
            # first row with any prose immediately before it.
            if out_lines and out_lines[-1].strip():
                out_lines.append('')
            out_lines.extend(fixed)
            out_lines.append('')
        else:
            out_lines.append(line)
    return '\n'.join(out_lines)


# ---------------------------------------------------------------------------
# Y.Doc → markdown
# ---------------------------------------------------------------------------

def ydoc_update_to_markdown(update: bytes | None) -> str:
    """Apply `update` into a fresh Y.Doc and return its markdown.

    `None` or empty bytes → empty string, matching an empty editor.

    The Text when it holds anything; otherwise the legacy XmlFragment
    (a document seeded before the CodeMirror editor) rendered to
    markdown.
    """
    if not update:
        return ''
    doc = Doc()
    text = Text()
    doc[_MARKDOWN_FIELD] = text
    frag = XmlFragment()
    doc[_PROSEMIRROR_FIELD] = frag
    doc.apply_update(bytes(update))

    content = str(text)
    if content:
        return content
    return _render_fragment(frag)


def ydoc_update_is_legacy_tree(update: bytes | None) -> bool:
    """Whether `update` holds a legacy ProseMirror tree and no text."""
    if not update:
        return False
    doc = Doc()
    text = Text()
    doc[_MARKDOWN_FIELD] = text
    frag = XmlFragment()
    doc[_PROSEMIRROR_FIELD] = frag
    doc.apply_update(bytes(update))
    return not str(text) and bool(len(frag.children))


def _render_fragment(frag: XmlFragment) -> str:
    """The markdown of a legacy ProseMirror tree."""
    lines: list[str] = []
    for child in list(frag.children):
        _render_block(child, lines, list_context=None)
    # Trim trailing blank lines but keep the terminal newline convention.
    while lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines) + ('\n' if lines else '')


def normalize_markdown(md: str | None) -> str:
    """Return `md` as the editor would hold it after seeding.

    Seeding only cleans legacy shapes (and line endings): comparing
    cleaned forms tells a real edit apart from that cleanup.
    """
    return _clean_markdown(md)


# Matches a hardBreak as `_render_inline_children` emits it (`'  \n'`,
# plus any whitespace the neighbouring text contributed) so callers that
# can't host a line break — headings — can collapse it back to a space.
_HARD_BREAK_RE = re.compile(r'[ \t]*\n[ \t]*')


def _render_block(node, lines: list[str], *, list_context) -> None:
    """Append the markdown lines for a single block node to `lines`.

    `list_context` carries per-list state when we're inside a list
    (kind='ul'|'ol', counter for numbered lists, indent depth). None
    at the top level.
    """
    if isinstance(node, XmlText):
        # Stray XmlText at block level — very rare, but render as a
        # bare paragraph so we don't drop content.
        lines.append(_render_text_with_marks(node))
        lines.append('')
        return

    tag = getattr(node, 'tag', None)

    if tag == 'heading':
        level = _int_attr(node, 'level', default=1)
        level = max(1, min(6, level))
        # ATX headings are single-line by definition. A hardBreak inside
        # one is reachable in the editor (Shift-Enter), so collapse it to
        # a space — emitting the second line raw would re-parse as a
        # separate paragraph and silently split the heading in two.
        text = _HARD_BREAK_RE.sub(' ', _render_inline_children(node))
        lines.append('#' * level + ' ' + text)
        lines.append('')
        return

    if tag == 'paragraph':
        text = _render_inline_children(node)
        if text.strip():
            # `_render_inline_children` emits `'  \n'` per hardBreak, so a
            # paragraph with line breaks comes back as a multi-line string.
            # Split it: `lines` is the unit that blockquote and list-item
            # rendering prefix/indent, and an embedded newline would slip
            # past that prefixing and break the block on re-parse.
            lines.extend(text.split('\n'))
            lines.append('')
        return

    if tag == 'bulletList':
        for child in list(node.children):
            _render_list_item(child, lines, marker='- ')
        lines.append('')
        return

    if tag == 'orderedList':
        start = _int_attr(node, 'start', default=1)
        idx = start
        for child in list(node.children):
            _render_list_item(child, lines, marker=f'{idx}. ')
            idx += 1
        lines.append('')
        return

    if tag == 'blockquote':
        inner: list[str] = []
        for child in list(node.children):
            _render_block(child, inner, list_context=None)
        while inner and inner[-1] == '':
            inner.pop()
        for line in inner:
            lines.append('> ' + line if line else '>')
        lines.append('')
        return

    if tag == 'codeBlock':
        lang = _string_attr(node, 'language', default='') or ''
        lines.append('```' + lang)
        # `codeBlock`'s children are XmlText nodes with the raw code.
        for child in list(node.children):
            if isinstance(child, XmlText):
                for code_line in _plain_text(child).splitlines() or ['']:
                    lines.append(code_line)
        lines.append('```')
        lines.append('')
        return

    if tag == 'horizontalRule':
        lines.append('---')
        lines.append('')
        return

    if tag == 'image':
        src = _string_attr(node, 'src', default='')
        alt = _string_attr(node, 'alt', default='')
        title = _string_attr(node, 'title', default='')
        title_part = f' "{title}"' if title else ''
        lines.append(f'![{alt}]({src}{title_part})')
        lines.append('')
        return

    if tag == 'table':
        _render_table(node, lines)
        return

    # Unknown block: render nothing rather than crashing. Loud in the
    # log so a missing case here doesn't silently eat content.
    logger.warning('collab render: unknown block tag %r — skipped', tag)


def _render_table(node, lines: list[str]) -> None:
    """Render a `table` XmlElement as a GFM pipe table.

    GFM requires the first row to be the header and the second row to
    be a separator (`| --- | --- |`). TipTap's schema tags header cells
    as `tableHeader` and body cells as `tableCell`, and doesn't require
    the header to be the first row — but pipe syntax has no way to
    express a mid-table header. We handle both shapes pragmatically:
      * If the first row is all `tableHeader`, treat it as the header
        and emit the separator after it (round-trip friendly).
      * Otherwise, synthesise an empty header row above the data so
        the output re-parses as a valid GFM table. This is lossy for
        the visual "no header" shape, but the alternative is emitting
        raw pipes that a markdown parser will read as a paragraph and
        we lose the tabular structure entirely.

    Cells are joined with pipes; internal pipes get escaped as `\\|`
    to keep the row shape parseable. Multi-line cell content is
    collapsed to a single line joined with `<br>` — GFM tables can't
    contain block content between the pipes.
    """
    rows: list[tuple[bool, list[str]]] = []
    for row in list(node.children):
        if not isinstance(row, XmlElement) or row.tag != 'tableRow':
            continue
        cells: list[str] = []
        all_headers = True
        for cell in list(row.children):
            if not isinstance(cell, XmlElement):
                continue
            if cell.tag not in ('tableHeader', 'tableCell'):
                continue
            if cell.tag != 'tableHeader':
                all_headers = False
            cell_lines: list[str] = []
            for child in list(cell.children):
                _render_block(child, cell_lines, list_context=None)
            while cell_lines and cell_lines[-1] == '':
                cell_lines.pop()
            # GFM cells are single-line: join intra-cell breaks with
            # `<br>`, and escape stray pipes so the row shape survives.
            text = '<br>'.join(line.strip() for line in cell_lines if line is not None)
            cells.append(text.replace('|', '\\|'))
        rows.append((all_headers and bool(cells), cells))

    if not rows:
        return

    # Column count: max cells across rows. Short rows get padded so
    # every emitted line has the same pipe count (a GFM parser is
    # forgiving here, but consistent output is easier on humans
    # reading the raw source column).
    cols = max(len(r[1]) for r in rows)
    if cols == 0:
        return

    def _emit(cells: list[str]) -> None:
        padded = cells + [''] * (cols - len(cells))
        lines.append('| ' + ' | '.join(padded) + ' |')

    if rows[0][0]:
        _emit(rows[0][1])
        lines.append('| ' + ' | '.join(['---'] * cols) + ' |')
        body_start = 1
    else:
        # Header-less TipTap table → synthesise an empty header row so
        # the output remains a valid GFM table when re-parsed.
        _emit([''] * cols)
        lines.append('| ' + ' | '.join(['---'] * cols) + ' |')
        body_start = 0

    for _, cells in rows[body_start:]:
        _emit(cells)
    lines.append('')


def _render_list_item(node, lines: list[str], marker: str) -> None:
    """Render a `listItem`'s children as a marker-prefixed list item.

    We render each block child, then indent all continuation lines to
    align under the marker column.
    """
    if not isinstance(node, XmlElement) or node.tag != 'listItem':
        return
    inner: list[str] = []
    for child in list(node.children):
        _render_block(child, inner, list_context=None)
    while inner and inner[-1] == '':
        inner.pop()
    if not inner:
        lines.append(marker.rstrip())
        return
    indent = ' ' * len(marker)
    lines.append(marker + inner[0])
    for line in inner[1:]:
        lines.append((indent + line) if line else '')


def _render_inline_children(block) -> str:
    """Render the inline children of a block node as a single markdown
    string, closing and reopening marks as the runs' formatting changes."""
    out: list[str] = []
    for child in list(block.children):
        if isinstance(child, XmlText):
            out.append(_render_text_with_marks(child))
        elif isinstance(child, XmlElement):
            if child.tag == 'hardBreak':
                out.append('  \n')
            elif child.tag == 'image':
                src = _string_attr(child, 'src', default='')
                alt = _string_attr(child, 'alt', default='')
                title = _string_attr(child, 'title', default='')
                title_part = f' "{title}"' if title else ''
                out.append(f'![{alt}]({src}{title_part})')
            elif child.tag in MENTION_NODE_NAMES:
                # The chip as the markdown span the editor reads.
                kind = _string_attr(child, 'kind', default='')
                if kind not in MENTION_KINDS:
                    kind = MENTION_DEFAULT_KIND_BY_NODE[child.tag]
                out.append(build_mention_span(
                    kind,
                    _string_attr(child, 'id', default=''),
                    _string_attr(child, 'label', default=''),
                ))
            # Other inline elements are unexpected; ignore.
    return ''.join(out)


def _plain_text(node: XmlText) -> str:
    """The text of `node` without its formatting.

    Not `str(node)`: pycrdt renders formatted runs as pseudo-XML
    (`<bold>x</bold>`, `<link href="…">x</link>`).
    """
    return ''.join(chunk for chunk, _ in node.diff() if isinstance(chunk, str))


def _text_marks(formatting: dict) -> dict[str, dict]:
    """Normalise Yjs formatting into `{mark name: mark attrs}`."""
    marks = {}
    for key, value in formatting.items():
        # y-prosemirror suffixes marks that may overlap themselves with
        # `--<hash>`; none of the ones we render do, but strip it anyway.
        name = key.split('--', 1)[0]
        if isinstance(value, str):
            # Documents seeded before marks were written as formatting
            # carry them as node attributes: 'true', or JSON for links.
            try:
                value = json.loads(value)
            except ValueError:
                value = {}
        marks[name] = value if isinstance(value, dict) else {}
    return marks


def _emphasize(text: str, markers: list[str]) -> str:
    """Wrap already-escaped `text` in emphasis `markers`, innermost first.

    Leading and trailing whitespace stays outside the markers:
    `** bold**` is not bold in CommonMark, and a run typed after a space
    starts with one.
    """
    core = text.strip()
    if not core or not markers:
        return text
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    for marker in markers:
        core = f'{marker}{core}{marker}'
    return f'{lead}{core}{trail}'


def _markdown_link(label: str, href: str, title: str | None) -> str:
    if not href:
        return label
    title_part = f' "{_escape_link_title(title)}"' if title else ''
    return f'[{label}]({href}{title_part})'


def _escape_link_title(title: str) -> str:
    return title.replace('\\', '\\\\').replace('"', '\\"')


def _render_text_with_marks(node: XmlText) -> str:
    """Render an XmlText node's formatted runs as markdown.

    y-prosemirror stores marks as Yjs formatting on each run of text,
    so the node is walked run by run (`diff()`). Code excludes every
    other mark in TipTap; bold/italic/strike nest inside a link, and
    consecutive runs under the same link share one `[…](…)`.
    """
    # Documents seeded before marks were written as formatting carry
    # them as attributes on the whole node.
    node_marks = dict(node.attributes)
    runs = []
    for chunk, formatting in node.diff():
        if not isinstance(chunk, str) or not chunk:
            continue
        marks = _text_marks({**node_marks, **(formatting or {})})
        link = marks.pop('link', None)
        if 'code' in marks:
            text = f'`{chunk}`'
        else:
            # Escape markdown special chars in raw text before we apply
            # marks (otherwise `**` in normal prose would render as bold
            # on next parse).
            markers = [marker for name, marker in (('strike', '~~'), ('italic', '*'), ('bold', '**'))
                       if name in marks]
            text = _emphasize(_escape_markdown(chunk), markers)
        runs.append((link, text))

    out = []
    for link, group in groupby(runs, key=lambda run: run[0]):
        text = ''.join(run_text for _, run_text in group)
        if link is not None:
            text = _markdown_link(text, link.get('href') or '', link.get('title'))
        out.append(text)
    return ''.join(out)


# Characters that need escaping when they appear MID-TEXT to prevent
# a re-parse from interpreting them as markdown syntax. Deliberately
# narrower than the full CommonMark set — we don't blanket-escape `.`
# `!` `-` etc. because those only matter at line starts and their
# escaped form (`\.` `\!` `\-`) shows up as ugly noise in the raw
# source column readers see (REST, exports, git-tracked backups).
# Chars kept out and their justification:
#   `.`  only meaningful after digit + at line start (numbered list)
#   `!`  only meaningful when followed by `[` (image)
#   `-`  only meaningful at line start (unordered list / hr)
#   `+`  same as `-`
#   `>`  only meaningful at line start (blockquote)
#   `#`  only meaningful at line start (heading)
# These edge cases are handled by escaping only when the character
# lands at position 0 of a fresh line. Everything else is safe raw.
_MD_INLINE_ESCAPE_CHARS = r'\`*_[]<'


def _escape_markdown(text: str) -> str:
    r"""Prefix markdown-special characters with a backslash so a re-parse
    round-trips them as literal text.

    Kept intentionally narrow: only the chars that would change meaning
    if left raw INSIDE inline text. Chars that only matter at line
    starts (heading `#`, list `-`/`+`/`.`, blockquote `>`, hr `---`)
    are handled by the block renderer emitting them on their own lines
    where they can't collide with user text; escaping them everywhere
    would produce `\-hello` for a paragraph that begins with a hyphen,
    which is technically correct but visually wrong.
    """
    out = []
    for ch in text:
        if ch in _MD_INLINE_ESCAPE_CHARS:
            out.append('\\')
        out.append(ch)
    return ''.join(out)


def _int_attr(node, name: str, default: int) -> int:
    try:
        return int(node.attributes[name])
    except (KeyError, TypeError, ValueError):
        return default


def _string_attr(node, name: str, default: str) -> str:
    try:
        return str(node.attributes[name])
    except (KeyError, TypeError):
        return default
