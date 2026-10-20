#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""The mention-chip markup contract, shared by every server-side writer.

The editor in iris-frontend stores an inline reference to another IRIS
object as a structured HTML span inside the markdown source column:

    <span data-mention data-kind="note" data-id="7" data-label="Runbook">#Runbook</span>

Anything the backend writes into a markdown column that should render as
a chip MUST go through `build_mention_span` here: the editor only turns
this exact shape into a chip, anything else shows as literal text.

The `kind` → node-name mapping is the TipTap nodes of the editor before
it moved to CodeMirror: `collab/render.py` still reads them out of the
documents seeded back then (y-prosemirror named each XmlElement after its
ProseMirror node type).

Note this module is deliberately free of user-notification concerns.
`iris_engine/notifications/mentions.py` parses the same spans, but only
ever for `data-kind="user"` / `"team"`, and stays independent.
"""

from __future__ import annotations

import html


# Every chip kind the frontend knows how to render. Kept in sync with
# `KIND_STYLE` in `MarkDown/mention-kinds.ts`.
MENTION_KINDS = frozenset({
    'user',
    'team',
    'asset',
    'ioc',
    'note',
    'task',
    'datastore',
    'alert',
})

# `@` addresses people, `#` addresses objects. Only used for the span's
# text body — the rendered chip draws an icon instead, and the char is
# what a reader sees if the chip ever degrades to plain text.
_MENTION_CHAR_BY_KIND = {
    'user': '@',
    'team': '@',
}
_DEFAULT_MENTION_CHAR = '#'

# Legacy ProseMirror node type behind each kind. Two nodes covered all of
# them: `userMention` (the `@` trigger) and `caseMention` (the `#` trigger).
MENTION_NODE_BY_KIND = {
    'user': 'userMention',
    'team': 'userMention',
    'asset': 'caseMention',
    'ioc': 'caseMention',
    'note': 'caseMention',
    'task': 'caseMention',
    'datastore': 'caseMention',
    'alert': 'caseMention',
}

MENTION_NODE_NAMES = frozenset(MENTION_NODE_BY_KIND.values())

# Default kind per legacy node, for nodes whose `kind` is missing or bogus.
MENTION_DEFAULT_KIND_BY_NODE = {
    'userMention': 'user',
    'caseMention': 'asset',
}


def build_mention_span(kind: str, object_id, label: str) -> str:
    """Render the chip markup for `label` pointing at `object_id`.

    Both `label` and `object_id` are HTML-escaped. The escalation call
    sites pass a plain int id and a column value for the label, but this
    is also the legacy tree reader's renderer (`render.py`'s
    `_render_inline_children`), where both values come straight off a
    Y.Doc attribute — i.e. off the wire from a browser. They
    land inside attribute values that the frontend reads back with
    `getAttribute`, so neither may be able to close the quote.
    """
    safe_kind = kind if kind in MENTION_KINDS else 'user'
    safe_label = html.escape(str(label), quote=True)
    safe_id = html.escape(str(object_id), quote=True)
    char = _MENTION_CHAR_BY_KIND.get(safe_kind, _DEFAULT_MENTION_CHAR)
    return (f'<span data-mention data-kind="{safe_kind}" data-id="{safe_id}" '
            f'data-label="{safe_label}">{char}{safe_label}</span>')
