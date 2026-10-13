#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the case-scoped Notes domain."""
from __future__ import annotations

from marshmallow import ValidationError

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    PAGINATION_SCHEMA_FRAGMENT,
    VIEW_SCHEMA_FRAGMENT,
    build_pagination,
    clip_text_field,
    dump_paginated,
    schema_for_view,
)
from app.business.notes import (
    notes_create,
    notes_get,
    notes_search,
    notes_update,
)
from app.iris_engine.collab.sync import collab_current_markdown
from app.iris_engine.collab.sync import collab_replace_markdown
from app.business.notes_directories import (
    notes_directories_create,
    notes_directories_filter,
    notes_directories_get_or_create_root,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseNoteDirectorySchema, CaseNoteSchema


_note_schema = CaseNoteSchema()
_directory_schema = CaseNoteDirectorySchema()

# Default projection for `iris_case_notes_list`. `notes_search` is not
# paginated, so this tool returns *every* matching note in the case —
# unprojected that is every note body plus every comment thread in one
# payload, easily the largest single response any tool can produce.
#
# `note_content` is kept but clipped to a preview: the search is
# free-text, so a result set with no content at all would leave the
# caller unable to tell which note matched and why.
_NOTE_SUMMARY_FIELDS = (
    'note_id',
    'note_title',
    'note_content',
    'note_creationdate',
    'note_lastupdate',
    'note_user',
    'directory_id',
    'directory.name',
)

_note_summary_schema = CaseNoteSchema(only=_NOTE_SUMMARY_FIELDS)

_NOTE_LIST_PREVIEWS = {'note_content': 400}

# `iris_case_notes_get` with `content_offset`: a chunk stays under the
# result budget's per-string clip (MAX_TEXT_FIELD_BYTES), a window
# under its total, escaping included in most cases
_NOTE_CHUNK_CHARS = 3000
_NOTE_WINDOW_CHARS = 30000


def _get_note_in_case(note_id: int, case_id: int):
    note = notes_get(note_id)
    if note.note_case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Note #{note_id} does not belong to case #{case_id}.')
    return note


@mcp_tool(
    name='iris_case_notes_list',
    description=(
        'Search case notes by free-text. Pass an empty string to list all '
        'notes in the case. Returns note summaries with the body clipped to '
        'a short preview — call `iris_case_notes_get` with a `note_id` from '
        'the result to read a note in full. Every match is returned unless '
        '`page` or `per_page` is given.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **VIEW_SCHEMA_FRAGMENT,
            'search_input': {'type': 'string'},
            'page': PAGINATION_SCHEMA_FRAGMENT['page'],
            'per_page': PAGINATION_SCHEMA_FRAGMENT['per_page'],
        },
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_list(args: dict) -> dict:
    search_input = (args.get('search_input') or '').strip() or '%'
    try:
        notes = notes_search(args['case_identifier'], search_input)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    paging = {}
    if args.get('page') is not None or args.get('per_page') is not None:
        # Paged here: notes_search is not. Opt-in, so the callers that
        # expect every note in one answer keep getting it. The search has
        # no order: by id, so that the pages do not overlap
        pagination = build_pagination(args)
        page = max(1, pagination.get_page())
        per_page = max(1, pagination.get_per_page())
        total = len(notes)
        notes = sorted(notes, key=lambda note: note.note_id)[(page - 1) * per_page:page * per_page]
        last_page = max(1, (total + per_page - 1) // per_page)
        paging = {'total': total, 'last_page': last_page, 'current_page': page,
                  'next_page': page + 1 if page < last_page else None}
    schema = schema_for_view(args, _note_summary_schema, _note_schema)
    rows = schema.dump(notes, many=True)
    for field, limit in _NOTE_LIST_PREVIEWS.items():
        clip_text_field(rows, field, limit)
    return {'notes': rows, **paging}


def _notes_content_window(note, content: str, offset: int) -> dict:
    """The body from `offset` on, as chunks short enough to pass the
    result budget untouched. Rows dropped by the budget come off the end:
    the caller resumes from offset + the length of the chunks it got."""
    offset = min(max(0, offset), len(content))
    window = content[offset:offset + _NOTE_WINDOW_CHARS]
    end = offset + len(window)
    return {
        'note_id': note.note_id,
        'note_title': note.note_title,
        'directory_id': note.directory_id,
        'content_length': len(content),
        'content_offset': offset,
        'content_next_offset': end if end < len(content) else None,
        'note_content_chunks': [window[i:i + _NOTE_CHUNK_CHARS]
                                for i in range(0, len(window), _NOTE_CHUNK_CHARS)],
    }


@mcp_tool(
    name='iris_case_notes_get',
    description=(
        'Fetch a single note by ID. Bodies longer than a few thousand '
        'characters are clipped: pass `content_offset` (0 first) to read the '
        'body as `note_content_chunks` instead, then call again from '
        '`content_offset` + the total length of the chunks received until '
        'that reaches `content_length`.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'note_identifier': {'type': 'integer'},
            'content_offset': {
                'type': 'integer',
                'minimum': 0,
                'description': 'Read the body from this character on, in chunks.',
            },
        },
        'required': ['note_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_get(args: dict) -> dict:
    try:
        note = _get_note_in_case(args['note_identifier'], args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Note not found.') from exc
    data = _note_schema.dump(note)
    # Live content: the column lags while someone has the note open,
    # and an edit built on it would revert their unsaved typing.
    data['note_content'] = collab_current_markdown(f'note:{note.note_id}',
                                                   data.get('note_content'))
    if args.get('content_offset') is not None:
        return _notes_content_window(note, data['note_content'] or '', int(args['content_offset']))
    return data


@mcp_tool(
    name='iris_case_notes_create',
    description=(
        'Create a note in a case. `directory_id` is optional — if omitted, '
        'the note is placed in the case top-level directory automatically '
        '(one is created when the case has no directory yet).'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Note fields.',
                'properties': {
                    'note_title': {
                        'type': 'string',
                        'maxLength': 155,
                        'description': 'Note title (short).',
                    },
                    'note_content': {
                        'type': 'string',
                        'description': 'Note body, markdown accepted.',
                    },
                    'directory_id': {
                        'type': 'integer',
                        'description': (
                            'Target directory id. Omit to place the note in '
                            'the case top-level directory (auto-resolved).'
                        ),
                    },
                    'custom_attributes': {
                        'type': 'object',
                        'description': 'Optional per-note custom attributes.',
                    },
                },
                'required': ['note_title', 'note_content'],
                'additionalProperties': False,
            },
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_create(args: dict) -> dict:
    try:
        payload = dict(args['payload'])
        case_id = args['case_identifier']
        # If the model didn't pick a directory (very common — the LLM
        # doesn't know the directory tree unless it also listed it), fall
        # back to the case's top-level directory. `verify_directory_id` in
        # the schema demands a non-null directory_id, so we resolve one
        # here — creating it when the case has an empty tree, which is the
        # default for any case not built from a template.
        if not payload.get('directory_id'):
            payload['directory_id'] = notes_directories_get_or_create_root(case_id).id
        _note_schema.verify_directory_id(payload, caseid=case_id)
        note = _note_schema.load(payload)
        note = notes_create(note, case_id)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _note_schema.dump(note)


@mcp_tool(
    name='iris_case_notes_directories_list',
    description=(
        'List the note directories (folders) of a case. Use it to resolve a '
        'folder name to the `directory_id` expected by iris_case_notes_create.'
    ),
    input_schema={
        'type': 'object',
        'properties': PAGINATION_SCHEMA_FRAGMENT,
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_directories_list(args: dict) -> dict:
    result = notes_directories_filter(args['case_identifier'], build_pagination(args))
    return dump_paginated(_directory_schema, result)


@mcp_tool(
    name='iris_case_notes_directories_create',
    description=(
        'Create a note directory (folder) in a case. Omit `parent_id` for a '
        'top-level directory, or pass the id of an existing directory of the '
        'same case to nest the new one under it.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'Directory name.'},
            'parent_id': {
                'type': 'integer',
                'description': (
                    'Parent directory id. Omit to create a top-level directory.'
                ),
            },
        },
        'required': ['name'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_directories_create(args: dict) -> dict:
    case_id = args['case_identifier']
    payload = {'name': args['name'], 'case_id': case_id}
    try:
        if args.get('parent_id') is not None:
            # Rejects a parent that belongs to another case, so a stray id
            # can't graft a directory onto someone else's tree.
            _directory_schema.verify_parent_id(args['parent_id'], case_id=case_id)
            payload['parent_id'] = args['parent_id']
        directory = _directory_schema.load(payload)
        notes_directories_create(directory)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Validation error: {exc.normalized_messages()}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _directory_schema.dump(directory)


@mcp_tool(
    name='iris_case_notes_update',
    description=(
        'Edit an existing case note (partial payload accepted). '
        '`payload.note_content` is the FULL new markdown body, not a diff '
        '— start from the content returned by iris_case_notes_get and keep '
        'every part that should stay. The previous version is kept in the '
        'note history.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'note_identifier': {'type': 'integer'},
            'payload': {
                'type': 'object',
                'properties': {
                    'note_title': {'type': 'string', 'minLength': 1},
                    'note_content': {
                        'type': 'string',
                        'description': 'Full new markdown body.',
                    },
                },
            },
        },
        'required': ['note_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_update(args: dict) -> dict:
    try:
        note = _get_note_in_case(args['note_identifier'], args['case_identifier'])
        payload = dict(args['payload'])
        payload['note_id'] = note.note_id
        _note_schema.load(payload, partial=True, instance=note)
        note = notes_update(iris_current_user, note)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Note not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Validation error: {exc.normalized_messages()}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    if 'note_content' in payload:
        # Once the note has been opened the Y.Doc is authoritative:
        # without this the editor keeps showing the old body and the
        # next flush writes it back over the column.
        collab_replace_markdown(f'note:{note.note_id}', note.note_content or '')
    return _note_schema.dump(note)
