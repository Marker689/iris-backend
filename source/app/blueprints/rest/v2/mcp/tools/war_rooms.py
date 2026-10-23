#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS

"""MCP tools for the War Rooms domain.

Yuki (the chatbot) uses these to read and mutate war-rooms when the
conversation is scoped to one. `war_room_scoped=True` on the decorator
adds a `war_room_id` slot to each tool's input schema, and dispatch
enforces access before invocation — the loop also overrides the id
with the conversation's scope on every call (see loop.py §7 for the
prompt-injection defence rationale).

Authorization mirrors `war_rooms/access.py`: reads declare
`war_rooms_read` and need `read_only` on the room, writes declare
`war_rooms_write` and need `full_access` (dispatch derives the level
from `mcp/classification.py`). Server admins pass either way, as they
do on the REST routes.

Read, creation (of a room too), update, case attachment, membership,
timeline, task, decision, sitrep and note-edit tools are exposed; the
scope (assets / IOCs of the attached cases, registry assets, staging)
lives in `war_room_scope.py`. Delete / archive / detach is deliberately
excluded — the analyst can perform those from the UI, and letting an LLM
propose them would be a common footgun. So is voting on a decision: a
vote has to be the approver's own act.

The war-room summary, its notes and its sitreps are collaborative
documents: reads go through `collab_current_markdown` and edits through
`collab_replace_markdown`, else an edit is reverted by the next flush of
an open editor.
"""
from __future__ import annotations

from datetime import datetime

from marshmallow import ValidationError

from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.access_controls import ac_current_user_can_read_vulnerabilities
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.war_rooms.access import WarRoomEventVisibility
from app.blueprints.rest.v2.war_rooms.access import war_room_readable_attached_case_ids
from app.blueprints.rest.v2.war_rooms.serializers import serialize_case_attachment
from app.blueprints.rest.v2.war_rooms.serializers import serialize_member
from app.iris_engine.collab.sync import collab_current_markdown
from app.iris_engine.collab.sync import collab_replace_markdown
from app.business import (
    war_room_chat as war_room_chat_biz,
    war_room_decisions as war_room_decisions_biz,
    war_room_notes as war_room_notes_biz,
    war_room_board as war_room_board_biz,
    war_room_datastore as war_room_datastore_biz,
    war_room_linked_case_timelines as war_room_linked_case_timelines_biz,
    war_room_sitreps as war_room_sitreps_biz,
    war_room_tasks as war_room_tasks_biz,
    war_room_teams as war_room_teams_biz,
    war_room_timelines as war_room_timelines_biz,
    war_rooms as war_rooms_biz,
)
from app.business.users import users_get_active
from app.models.authorization import CaseAccessLevel
from app.models.authorization import WarRoomAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError


# ---- War-room top-level --------------------------------------------

@mcp_tool(
    name='iris_war_rooms_list',
    description='List war rooms the current user has access to.',
    input_schema={
        'type': 'object',
        'properties': {
            'state': {
                'type': 'string',
                'description': "Filter by state (e.g. 'open', 'closed').",
            },
            'search': {
                'type': 'string',
                'description': 'Free-text match on name / description.',
            },
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    mvp=True,
)
def iris_war_rooms_list(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        rooms = war_rooms_biz.war_room_list_for_user(
            user_id=user_obj.id,
            is_admin=False,
            state=args.get('state'),
            search=args.get('search'),
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'war_rooms': [_room_summary(r) for r in rooms]}


@mcp_tool(
    name='iris_war_rooms_get',
    description='Fetch a war room by id.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_rooms_get(args: dict) -> dict:
    try:
        room = war_rooms_biz.war_room_get(args['war_room_id'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'War room not found.') from exc
    data = _room_summary(room)
    # Live summary: the column lags while someone has the room open.
    data['description'] = collab_current_markdown(f'war-room-summary:{room.war_room_id}',
                                                  data['description'])
    return data


@mcp_tool(
    name='iris_war_rooms_update',
    description=(
        'Update a war room: rename it, change its state (open, active, standby, closed) or '
        'severity (severity_id from iris_taxonomies_list), and / or replace its summary. '
        '`description` is the FULL new markdown summary, not a diff — start from the one '
        'returned by iris_war_rooms_get. Omitted fields are left unchanged.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'description': {'type': 'string', 'description': 'Full new markdown summary.'},
            'state': {'type': 'string', 'enum': ['open', 'active', 'standby', 'closed']},
            'severity_id': {'type': 'integer'},
        },
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_rooms_update(args: dict) -> dict:
    if all(args.get(key) is None for key in ('name', 'description', 'state', 'severity_id')):
        raise MCPError(protocol.INVALID_PARAMS,
                       'Nothing to update: provide a name, description, state and / or severity_id.')
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    try:
        previous_state = war_rooms_biz.war_room_get(war_room_id).state
        room = war_rooms_biz.war_room_update(
            war_room_id,
            name=args.get('name'),
            description=args.get('description'),
            state=args.get('state'),
            severity_id=args.get('severity_id'),
            closed_by_id=user_obj.id,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'War room not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    if args.get('description') is not None:
        # Once the room has been opened the summary's Y.Doc is
        # authoritative: without this the next flush reverts the edit.
        collab_replace_markdown(f'war-room-summary:{room.war_room_id}', args['description'])
    if room.state != previous_state:
        war_room_chat_biz.emit_system_event(
            war_room_id, 'system', f'War room state changed to {room.state}',
            author_id=user_obj.id,
        )
    return _room_summary(room)


@mcp_tool(
    name='iris_war_room_cases_list',
    description=(
        'List the cases attached to a war room, with their customer, owner, state, open / '
        'close dates, task counts and the attachment note. `readable` is false for the cases '
        'you have no access to: their content is out of reach of every other tool.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_cases_list(args: dict) -> dict:
    war_room_id = args['war_room_id']
    readable = set(war_room_readable_attached_case_ids(war_room_id))
    cases = []
    for row in war_rooms_biz.war_room_cases_list(war_room_id):
        case = serialize_case_attachment(row)
        case['readable'] = row.case_id in readable
        cases.append(case)
    return {'cases': cases}


@mcp_tool(
    name='iris_war_room_members_list',
    description='List the members of a war room with their role.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_members_list(args: dict) -> dict:
    rows = war_rooms_biz.war_room_members_list(args['war_room_id'])
    return {'members': [serialize_member(r) for r in rows]}


@mcp_tool(
    name='iris_war_room_board_get',
    description=(
        'War room board: per attached case the asset counts by flag and compromise status, '
        'the decisions, and the attention list (compromised assets, exceptions, overdue '
        'decisions). Only the attached cases the caller can read are counted.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_board_get(args: dict) -> dict:
    war_room_id = args['war_room_id']
    return war_room_board_biz.war_room_board_build(
        war_room_id, war_room_readable_attached_case_ids(war_room_id),
        include_vulnerabilities=ac_current_user_can_read_vulnerabilities(),
    )


@mcp_tool(
    name='iris_war_room_teams_list',
    description='List the teams of a war room, with the user ids of their members.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_teams_list(args: dict) -> dict:
    war_room_id = args['war_room_id']
    teams = []
    for team in war_room_teams_biz.war_room_team_list(war_room_id):
        member_ids = war_room_teams_biz.war_room_team_member_user_ids(war_room_id, [team.team_id])
        teams.append({
            'team_id': team.team_id,
            'name': team.name,
            'description': team.description,
            'member_ids': sorted(member_ids),
        })
    return {'teams': teams}


@mcp_tool(
    name='iris_war_room_datastore_list',
    description=(
        'List the files of the war room datastore (name, description, size, type, sha256, '
        'tags). The files of the attached cases are in their own case datastore.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_datastore_list(args: dict) -> dict:
    rows = war_room_datastore_biz.war_room_datastore_list(args['war_room_id'])
    return {'files': [{
        'file_id': r.file_id,
        'filename': r.filename,
        'description': r.description,
        'size_bytes': r.size_bytes,
        'mime_type': r.mime_type,
        'sha256': r.sha256,
        'tags': r.tags,
        'uploaded_at': _iso(r.uploaded_at),
        'uploaded_by_id': r.uploaded_by_id,
    } for r in rows]}


@mcp_tool(
    name='iris_war_rooms_create',
    description=(
        'Create a war room. You become its lead, with full access; add the other people with '
        'iris_war_room_members_add and the cases with iris_war_room_cases_attach. state is one of '
        'open (default), active, standby, closed; severity_id comes from iris_taxonomies_list.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'description': {'type': 'string'},
            'state': {'type': 'string', 'enum': ['open', 'active', 'standby', 'closed']},
            'severity_id': {'type': 'integer'},
        },
        'required': ['name'],
    },
    permissions=(Permissions.war_rooms_create, Permissions.server_administrator),
    mvp=True,
)
def iris_war_rooms_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        room = war_rooms_biz.war_room_create(
            name=args.get('name'),
            description=args.get('description'),
            state=args.get('state'),
            severity_id=args.get('severity_id'),
            created_by_id=user_obj.id,
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _room_summary(room)


@mcp_tool(
    name='iris_war_room_cases_attach',
    description=(
        'Attach a case to a war room. Needs full access on the case. Attaching a case already '
        'attached only updates its note.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'case_id': {'type': 'integer'},
            'note': {'type': 'string', 'description': 'Why the case is in the room.'},
        },
        'required': ['case_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_cases_attach(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    case_id = args.get('case_id')
    # Same gate as the REST route: write access on the room is not
    # enough, attaching hands the case to everyone in the room.
    if not ac_fast_check_current_user_has_case_access(case_id, [CaseAccessLevel.full_access]):
        raise MCPError(protocol.IRIS_ACCESS_DENIED, f'No full access to case #{case_id}.')
    note = args.get('note') or None
    try:
        link = war_rooms_biz.war_room_attach_case(war_room_id, case_id, attached_by_id=user_obj.id, note=note)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    war_room_chat_biz.emit_system_event(
        war_room_id, 'case_attached',
        f'Attached case #{case_id}' + (f' — {note}' if note else ''),
        author_id=user_obj.id,
        ref_type='case', ref_id=case_id, ref_case_id=case_id,
        activity_type='case.attached',
    )
    return {
        'war_room_id': link.war_room_id,
        'case_id': link.case_id,
        'attached_at': _iso(link.attached_at),
        'note': link.note,
    }


_MEMBER_ACCESS_LEVELS = {
    'full_access': WarRoomAccessLevel.full_access.value,
    'read_only': WarRoomAccessLevel.read_only.value,
}


@mcp_tool(
    name='iris_war_room_members_add',
    description=(
        'Add a user to a war room, or change the role and access of a member. role is lead, '
        'responder (default) or observer; access is full_access (default) or read_only.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'user_id': {'type': 'integer'},
            'role': {'type': 'string', 'enum': ['lead', 'responder', 'observer']},
            'access': {'type': 'string', 'enum': sorted(_MEMBER_ACCESS_LEVELS)},
        },
        'required': ['user_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
)
def iris_war_room_members_add(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    user_id = args.get('user_id')
    try:
        users_get_active(user_id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'No active user #{user_id}.') from exc
    try:
        war_rooms_biz.war_room_add_member(
            war_room_id, user_id,
            role=args.get('role'),
            added_by_id=user_obj.id,
            access_level=_MEMBER_ACCESS_LEVELS[args.get('access') or 'full_access'],
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    member = next((r for r in war_rooms_biz.war_room_members_list(war_room_id) if r.user_id == user_id), None)
    if member is None:
        raise MCPError(protocol.INTERNAL_ERROR, f'User #{user_id} was not added.')
    return {
        'war_room_id': war_room_id,
        'user_id': member.user_id,
        'user_login': member.login,
        'user_name': member.name,
        'role': member.role,
        'access': args.get('access') or 'full_access',
    }


# ---- Timelines -----------------------------------------------------

@mcp_tool(
    name='iris_war_room_timelines_list',
    description=(
        'List the native timelines of a war room. Their events come from '
        'iris_war_room_timeline_events_list; the timelines of the attached cases from '
        'iris_war_room_linked_case_timelines_list.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_timelines_list(args: dict) -> dict:
    rows = war_room_timelines_biz.list_timelines(args['war_room_id'])
    return {'timelines': [{
        'timeline_id': t.timeline_id,
        'name': t.name,
        'description': t.description,
        'is_default': bool(t.is_default),
        'created_at': _iso(t.created_at),
    } for t in rows]}


@mcp_tool(
    name='iris_war_room_timeline_events_list',
    description=(
        'List the events of the native war-room timelines, oldest first. Restrict to some '
        'timelines with timeline_ids (from iris_war_room_timelines_list).'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'timeline_ids': {'type': 'array', 'items': {'type': 'integer'}},
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_timeline_events_list(args: dict) -> dict:
    rows = WarRoomEventVisibility().filter(war_room_timelines_biz.list_timeline_events(
        args['war_room_id'], timeline_ids=args.get('timeline_ids') or None))
    return {'events': [_timeline_event(e) for e in rows]}


def _parse_iso_datetime(raw, field):
    if raw is None or raw == '':
        return None
    try:
        # Accept both "YYYY-MM-DD" and full ISO.
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except (AttributeError, ValueError) as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'{field} must be an ISO-8601 date-time.') from exc


_TIMELINE_EVENT_FIELDS = {
    'title': {'type': 'string'},
    'content': {'type': 'string', 'description': 'Markdown description of the event.'},
    'event_date': {'type': 'string', 'description': 'ISO-8601 date-time (UTC if no offset).'},
    'event_tz': {'type': 'string', 'description': "Offset shown with the date, e.g. '+00:00'."},
    'category': {'type': 'string', 'maxLength': 64},
    'tags': {'type': 'string', 'description': 'Comma-separated tags.'},
    'is_flagged': {'type': 'boolean'},
}


@mcp_tool(
    name='iris_war_room_timeline_events_create',
    description=(
        'Add an event to a native war-room timeline (timeline_id from '
        'iris_war_room_timelines_list). A title or a content is required.'
    ),
    input_schema={
        'type': 'object',
        'properties': {'timeline_id': {'type': 'integer'}, **_TIMELINE_EVENT_FIELDS},
        'required': ['timeline_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_timeline_events_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_timelines_biz.create_timeline_event(
            args['war_room_id'], args['timeline_id'],
            title=args.get('title'),
            content=args.get('content'),
            event_date=_parse_iso_datetime(args.get('event_date'), 'event_date'),
            event_tz=args.get('event_tz'),
            category=args.get('category'),
            tags=args.get('tags'),
            is_flagged=bool(args.get('is_flagged', False)),
            created_by_id=user_obj.id,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Timeline #{args["timeline_id"]} not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _timeline_event(row)


@mcp_tool(
    name='iris_war_room_timeline_events_update',
    description=(
        'Update an event of a native war-room timeline. Only the fields given are changed; '
        'timeline_id moves the event to another timeline of the room.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'event_id': {'type': 'integer', 'description': 'The `id` of the event.'},
            'timeline_id': {'type': 'integer'},
            **_TIMELINE_EVENT_FIELDS,
        },
        'required': ['event_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_timeline_events_update(args: dict) -> dict:
    # Linking case events, assets or IOCs is left to the UI: those ids
    # point into the attached cases and are not access-checked here.
    fields = {k: args[k] for k in ('timeline_id', *_TIMELINE_EVENT_FIELDS) if k in args}
    if not fields:
        raise MCPError(protocol.INVALID_PARAMS, 'Nothing to update.')
    if 'event_date' in fields:
        fields['event_date'] = _parse_iso_datetime(fields['event_date'], 'event_date')
    try:
        # An event a tracker restricted to other readers does not exist
        # for this caller.
        if not WarRoomEventVisibility().visible(
                war_room_timelines_biz.get_timeline_event(args['war_room_id'], args['event_id'])):
            raise ObjectNotFoundError()
        row = war_room_timelines_biz.update_timeline_event(
            args['war_room_id'], args['event_id'], **fields)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Event #{args["event_id"]} (or its timeline) not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _timeline_event(row)


@mcp_tool(
    name='iris_war_room_linked_case_timelines_list',
    description=(
        'List the timelines of the cases attached to the war room, grouped by case. Pass '
        'their ids to iris_war_room_linked_case_events_list to read the events. Cases you '
        'cannot read are left out.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_linked_case_timelines_list(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    return {'cases': war_room_linked_case_timelines_biz.list_linked_case_timelines(
        args['war_room_id'], user_obj.id)}


@mcp_tool(
    name='iris_war_room_linked_case_events_list',
    description=(
        'List the events of case timelines attached to the war room, shaped like the native '
        'war-room events. case_timeline_ids come from iris_war_room_linked_case_timelines_list.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'case_timeline_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
        },
        'required': ['case_timeline_ids'],
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_linked_case_events_list(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    return {'events': war_room_linked_case_timelines_biz.list_linked_case_events(
        args['war_room_id'], user_obj.id, args['case_timeline_ids'])}


# ---- Chat ----------------------------------------------------------

@mcp_tool(
    name='iris_war_room_chat_list',
    description=(
        'List the latest messages in a war room stream. Merges chat rows '
        'with the attached cases\' UserActivity — same view the UI shows.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'description': 'Max messages to return (default 50).',
            },
            'search': {
                'type': 'string',
                'description': 'Free-text substring match on message body.',
            },
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_chat_list(args: dict) -> dict:
    try:
        rows = war_room_chat_biz.list_messages(
            war_room_id=args['war_room_id'],
            limit=args.get('limit') or 50,
            search=args.get('search'),
            readable_case_ids=war_room_readable_attached_case_ids(args['war_room_id']),
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'messages': [_chat_row(m) for m in rows]}


@mcp_tool(
    name='iris_war_room_chat_post',
    description=(
        'Post a chat message to a war room. Plain text body; markdown '
        'is rendered by the client. Never attach files this way — file '
        'attachments require the drag-drop UI path.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'body': {
                'type': 'string',
                'minLength': 1,
                'description': 'Message text. Markdown accepted.',
            },
        },
        'required': ['body'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_chat_post(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_chat_biz.create_message(
            war_room_id=args['war_room_id'],
            author_id=user_obj.id,
            body=args['body'],
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _chat_row(row)


# ---- Sitreps -------------------------------------------------------

@mcp_tool(
    name='iris_war_room_sitreps_list',
    description='List sitreps in a war room, newest first.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_sitreps_list(args: dict) -> dict:
    rows = war_room_sitreps_biz.sitrep_list(args['war_room_id'])
    # Live body: the column lags while someone has the sitrep open.
    return {'sitreps': [_sitrep(s, live=True) for s in rows]}


@mcp_tool(
    name='iris_war_room_sitreps_create',
    description=(
        'Draft a new sitrep. Body accepts markdown. Draft only — an '
        'analyst still has to publish it from the UI.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'body_md': {'type': 'string'},
        },
        'required': ['title'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_sitreps_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_sitreps_biz.sitrep_draft(
            war_room_id=args['war_room_id'],
            title=args['title'],
            body_md=args.get('body_md') or '',
            authored_by_id=user_obj.id,
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _sitrep(row)


@mcp_tool(
    name='iris_war_room_sitreps_update',
    description=(
        'Edit a sitrep: retitle it and / or replace its body. `body_md` is the FULL new '
        'markdown body, not a diff — start from the body returned by '
        'iris_war_room_sitreps_list. A published sitrep can be corrected too; the snapshot '
        'taken at publication is kept.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'sitrep_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1},
            'body_md': {'type': 'string', 'description': 'Full new markdown body.'},
        },
        'required': ['sitrep_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_sitreps_update(args: dict) -> dict:
    if args.get('title') is None and args.get('body_md') is None:
        raise MCPError(protocol.INVALID_PARAMS, 'Nothing to update: provide a title and/or body_md.')
    try:
        row = war_room_sitreps_biz.sitrep_update(
            args['war_room_id'], args['sitrep_id'],
            title=args.get('title'),
            body_md=args.get('body_md'),
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Sitrep #{args["sitrep_id"]} not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    if args.get('body_md') is not None:
        # Same as the notes: the Y.Doc wins on the next flush.
        collab_replace_markdown(f'sitrep:{row.sitrep_id}', args['body_md'])
    return _sitrep(row)


# ---- Notes ---------------------------------------------------------

@mcp_tool(
    name='iris_war_room_notes_list',
    description='List notes in a war room.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_list(args: dict) -> dict:
    rows = war_room_notes_biz.war_room_note_list(args['war_room_id'])
    # Live content: the column lags while someone has the note open,
    # and an edit built on it would revert their unsaved typing.
    return {'notes': [_note(n, live=True) for n in rows]}


@mcp_tool(
    name='iris_war_room_notes_create',
    description='Create a note in a war room. Body accepts markdown.',
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'content': {'type': 'string'},
        },
        'required': ['title'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_notes_biz.war_room_note_create(
            war_room_id=args['war_room_id'],
            title=args['title'],
            content=args.get('content') or '',
            created_by_id=user_obj.id,
        )
    except (BusinessProcessingError, ValidationError) as exc:
        msg = exc.get_message() if isinstance(exc, BusinessProcessingError) else str(exc)
        raise MCPError(protocol.INVALID_PARAMS, msg) from exc
    war_room_chat_biz.emit_system_event(
        args['war_room_id'], 'note', f'Created note: {row.title}',
        author_id=user_obj.id, ref_type='war_room_note', ref_id=row.note_id,
    )
    return _note(row)


@mcp_tool(
    name='iris_war_room_notes_update',
    description=(
        'Edit an existing war-room note: rename it and/or replace its '
        'body. `content` is the FULL new markdown body, not a diff — '
        'start from the content returned by iris_war_room_notes_list '
        'and keep every part that should stay. Omit a field to leave '
        'it unchanged. The previous version is kept in the note history.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'note_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1},
            'content': {
                'type': 'string',
                'description': 'Full new markdown body.',
            },
        },
        'required': ['note_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_update(args: dict) -> dict:
    if args.get('title') is None and args.get('content') is None:
        raise MCPError(protocol.INVALID_PARAMS,
                       'Nothing to update: provide a title and/or content.')
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_notes_biz.war_room_note_update(
            war_room_id=args['war_room_id'],
            note_id=args['note_id'],
            title=args.get('title'),
            content=args.get('content'),
            updated_by_id=user_obj.id,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Note #{args["note_id"]} not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    if args.get('content') is not None:
        # Once the note has been opened the Y.Doc is authoritative:
        # without this the editor keeps showing the old body and the
        # next flush writes it back over the column.
        collab_replace_markdown(f'war-room-note:{row.note_id}', args['content'])
    return _note(row)


# ---- Tasks ---------------------------------------------------------

@mcp_tool(
    name='iris_war_room_tasks_list',
    description='List tasks in a war room.',
    input_schema={
        'type': 'object',
        'properties': {
            'q': {'type': 'string', 'description': 'Free-text search.'},
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_tasks_list(args: dict) -> dict:
    rows = war_room_tasks_biz.war_room_task_list(
        war_room_id=args['war_room_id'],
        q=args.get('q'),
    )
    teams = war_room_tasks_biz.war_room_task_teams([t.task_id for t in rows])
    return {'tasks': [_task(t, teams.get(t.task_id)) for t in rows]}


@mcp_tool(
    name='iris_war_room_tasks_create',
    description='Create a task in a war room.',
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'description': {'type': 'string'},
            'team_ids': {
                'type': 'array', 'items': {'type': 'integer'},
                'description': 'Teams of this war room assigned to the task.',
            },
        },
        'required': ['title'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_tasks_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_tasks_biz.war_room_task_create(
            war_room_id=args['war_room_id'],
            title=args['title'],
            description=args.get('description'),
            created_by_id=user_obj.id,
            team_ids=args.get('team_ids'),
        )
    except (BusinessProcessingError, ValidationError) as exc:
        msg = exc.get_message() if isinstance(exc, BusinessProcessingError) else str(exc)
        raise MCPError(protocol.INVALID_PARAMS, msg) from exc
    war_room_chat_biz.emit_system_event(
        args['war_room_id'], 'task_assigned', f'Created task: {row.title}',
        author_id=user_obj.id, ref_type='war_room_task', ref_id=row.task_id,
    )
    return _task(row, war_room_tasks_biz.war_room_task_teams([row.task_id]).get(row.task_id))


_TASK_UPDATE_FIELDS = ('title', 'description', 'status_id', 'assignee_id', 'tags', 'team_ids')


@mcp_tool(
    name='iris_war_room_tasks_update',
    description=(
        'Update a war-room task. Only the fields given are changed. status_id comes from '
        'iris_taxonomies_list; assignee_id must be a member of the room; team_ids replaces the '
        'assigned teams; due_at is an ISO-8601 date (empty string clears it). closed=true '
        'closes the task, closed=false reopens it.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'task_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1},
            'description': {'type': 'string'},
            'status_id': {'type': 'integer'},
            'assignee_id': {'type': ['integer', 'null']},
            'tags': {'type': 'string', 'description': 'Comma-separated tags.'},
            'team_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'due_at': {'type': 'string'},
            'closed': {'type': 'boolean'},
        },
        'required': ['task_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_tasks_update(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    task_id = args['task_id']
    fields = {k: args[k] for k in _TASK_UPDATE_FIELDS if k in args}
    if 'due_at' in args:
        fields['due_at'] = _parse_iso_datetime(args['due_at'], 'due_at')
    if not fields and 'closed' not in args:
        raise MCPError(protocol.INVALID_PARAMS, 'Nothing to update.')
    try:
        task = war_room_tasks_biz.war_room_task_get(war_room_id, task_id)
        if fields:
            task = war_room_tasks_biz.war_room_task_update(
                war_room_id, task_id, updated_by_id=user_obj.id, **fields)
        if args.get('closed') is True and task.closed_at is None:
            task = war_room_tasks_biz.war_room_task_close(war_room_id, task_id, closed_by_id=user_obj.id)
            war_room_chat_biz.emit_system_event(
                war_room_id, 'task_completed', f'Closed task: {task.title}',
                author_id=user_obj.id, ref_type='war_room_task', ref_id=task.task_id,
            )
        elif args.get('closed') is False and task.closed_at is not None:
            task = war_room_tasks_biz.war_room_task_reopen(war_room_id, task_id)
            war_room_chat_biz.emit_system_event(
                war_room_id, 'task_assigned', f'Reopened task: {task.title}',
                author_id=user_obj.id, ref_type='war_room_task', ref_id=task.task_id,
            )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Task #{task_id} not found in this war room.') from exc
    except (BusinessProcessingError, ValidationError) as exc:
        msg = exc.get_message() if isinstance(exc, BusinessProcessingError) else str(exc)
        raise MCPError(protocol.INVALID_PARAMS, msg) from exc
    return _task(task, war_room_tasks_biz.war_room_task_teams([task.task_id]).get(task.task_id))


# ---- Decisions -----------------------------------------------------

_DECISION_CREATE_FIELDS = ('title', 'rationale', 'status', 'target_at', 'owner_id',
                           'approver_ids', 'case_ids')


def _decision_readable_case_ids(case_ids) -> set:
    if not case_ids:
        return set()
    return set(ac_fast_check_current_user_has_cases_access(
        list(case_ids), [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
    ))


def _decisions_payload(decisions) -> list:
    readable = _decision_readable_case_ids(
        war_room_decisions_biz.war_room_decisions_referenced_case_ids(decisions))
    return war_room_decisions_biz.war_room_decisions_serialize(decisions, readable)


@mcp_tool(
    name='iris_war_room_decisions_list',
    description=(
        'List the decision register (D-n) of a war room, newest first. '
        'Each decision carries its status, target date, overdue flag, '
        'approvers and linked cases / assets.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'status': {
                'type': 'array',
                'items': {
                    'type': 'string',
                    'enum': ['proposed', 'approved', 'rejected', 'superseded'],
                },
                'description': 'Only return decisions in these statuses.',
            },
            'q': {'type': 'string', 'description': 'Free-text match on the title.'},
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_decisions_list(args: dict) -> dict:
    status = args.get('status')
    if isinstance(status, str):
        status = [status]
    q = args.get('q')
    try:
        rows = war_room_decisions_biz.war_room_decisions_list(
            args['war_room_id'],
            status=status if isinstance(status, list) else None,
            q=q if isinstance(q, str) else None,
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return {'decisions': _decisions_payload(rows)}


@mcp_tool(
    name='iris_war_room_decisions_create',
    description=(
        'Register a decision in the war room decision register. Status '
        'defaults to proposed; target_at is an ISO-8601 date-time (UTC '
        'if no offset). case_ids must be cases attached to the room; '
        'approver_ids / owner_id must be users with access to the room.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'rationale': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['proposed', 'approved']},
            'target_at': {'type': 'string', 'description': 'ISO-8601 date-time.'},
            'owner_id': {'type': 'integer'},
            'approver_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'case_ids': {'type': 'array', 'items': {'type': 'integer'}},
        },
        'required': ['title'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_decisions_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    raw = {k: args[k] for k in _DECISION_CREATE_FIELDS if k in args}
    try:
        # Linking a case requires read access on it; an unreadable case
        # gets the same answer as an unattached one.
        case_ids = war_room_decisions_biz.war_room_decisions_parse_case_ids(raw.get('case_ids'))
        readable = _decision_readable_case_ids(case_ids)
        for case_id in case_ids:
            if case_id not in readable:
                raise BusinessProcessingError(f'Case #{case_id} is not attached to this war room')
        decision = war_room_decisions_biz.war_room_decisions_create(
            war_room_id, raw, created_by_id=user_obj.id)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    war_room_chat_biz.emit_system_event(
        war_room_id, 'decision', f'D-{decision.number} {decision.title}',
        author_id=user_obj.id,
        ref_type='war_room_decision', ref_id=decision.decision_id,
    )
    return _decisions_payload([decision])[0]


# Linking assets is left to the UI: it needs per-asset case access checks.
_DECISION_UPDATE_FIELDS = ('title', 'rationale', 'status', 'target_at', 'owner_id',
                           'approver_ids', 'case_ids')


@mcp_tool(
    name='iris_war_room_decisions_update',
    description=(
        'Update a decision of the register (decision_id from iris_war_room_decisions_list). '
        'Only the fields given are changed; approver_ids and case_ids replace the current '
        'lists. Voting is not possible from here: approvers vote in the UI.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'decision_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'rationale': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['proposed', 'approved', 'rejected', 'superseded']},
            'target_at': {'type': ['string', 'null'], 'description': 'ISO-8601 date-time.'},
            'owner_id': {'type': ['integer', 'null']},
            'approver_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'case_ids': {'type': 'array', 'items': {'type': 'integer'}},
        },
        'required': ['decision_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_decisions_update(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    decision_id = args['decision_id']
    raw = {k: args[k] for k in _DECISION_UPDATE_FIELDS if k in args}
    if not raw:
        raise MCPError(protocol.INVALID_PARAMS, 'Nothing to update.')
    try:
        decision = war_room_decisions_biz.war_room_decisions_get(war_room_id, decision_id)
        previous_status = decision.status
        if 'case_ids' in raw:
            # Same gate as the REST route: keeping an already-linked case
            # needs no access, linking a new one needs read access.
            already_linked = war_room_decisions_biz.war_room_decisions_linked_case_ids(decision_id)
            new_ids = [c for c in war_room_decisions_biz.war_room_decisions_parse_case_ids(raw['case_ids'])
                       if c not in already_linked]
            readable = _decision_readable_case_ids(new_ids)
            for case_id in new_ids:
                if case_id not in readable:
                    raise BusinessProcessingError(f'Case #{case_id} is not attached to this war room')
        decision = war_room_decisions_biz.war_room_decisions_update(
            war_room_id, decision_id, raw, updated_by_id=user_obj.id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Decision #{decision_id} not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    if decision.status != previous_status and decision.status in ('approved', 'rejected'):
        war_room_chat_biz.emit_system_event(
            war_room_id, 'system', f'D-{decision.number} {decision.status}: {decision.title}',
            author_id=user_obj.id,
            ref_type='war_room_decision', ref_id=decision.decision_id,
        )
    return _decisions_payload([decision])[0]


# ---- Serializers (kept local so we don't leak SQLAlchemy shapes) ---

def _room_summary(r) -> dict:
    return {
        'war_room_id': getattr(r, 'war_room_id', None),
        'name': getattr(r, 'name', None),
        'description': getattr(r, 'description', None),
        'state': getattr(r, 'state', None),
        'severity_id': getattr(r, 'severity_id', None),
        'created_at': _iso(getattr(r, 'created_at', None)),
        'archived_at': _iso(getattr(r, 'archived_at', None)),
    }


def _chat_row(m) -> dict:
    return {
        'message_id': getattr(m, 'message_id', None),
        'kind': getattr(m, 'kind', None),
        'body': getattr(m, 'body', None),
        'author_id': getattr(m, 'author_id', None),
        'created_at': _iso(getattr(m, 'created_at', None)),
    }


def _sitrep(s, live: bool = False) -> dict:
    body = getattr(s, 'body_md', None)
    if live:
        body = collab_current_markdown(f'sitrep:{s.sitrep_id}', body)
    return {
        'sitrep_id': getattr(s, 'sitrep_id', None),
        'version': getattr(s, 'version', None),
        'title': getattr(s, 'title', None),
        'body_md': body,
        'authored_by_id': getattr(s, 'authored_by_id', None),
        'authored_at': _iso(getattr(s, 'authored_at', None)),
        'published': bool(getattr(s, 'published', False)),
    }


def _note(n, live: bool = False) -> dict:
    content = getattr(n, 'content', None)
    if live:
        content = collab_current_markdown(f'war-room-note:{n.note_id}', content)
    return {
        'note_id': getattr(n, 'note_id', None),
        'title': getattr(n, 'title', None),
        'content': content,
        'created_by_id': getattr(n, 'created_by_id', None),
        'folder_id': getattr(n, 'folder_id', None),
        'created_at': _iso(getattr(n, 'created_at', None)),
    }


def _task(t, teams=None) -> dict:
    return {
        'task_id': getattr(t, 'task_id', None),
        'title': getattr(t, 'title', None),
        'description': getattr(t, 'description', None),
        'status_id': getattr(t, 'status_id', None),
        'assignee_id': getattr(t, 'assignee_id', None),
        'teams': teams or [],
        'due_at': _iso(getattr(t, 'due_at', None)),
        'closed_at': _iso(getattr(t, 'closed_at', None)),
        'created_by_id': getattr(t, 'created_by_id', None),
        'created_at': _iso(getattr(t, 'created_at', None)),
    }


def _timeline_event(e) -> dict:
    return {
        'id': e.id,
        'timeline_id': e.timeline_id,
        'parent_id': getattr(e, 'parent_id', None),
        'case_id': e.case_id,
        'event_id': e.event_id,
        'title': e.title,
        'content': e.content,
        'event_date': _iso(e.event_date),
        'event_tz': e.event_tz,
        'category': getattr(e, 'category', None),
        'tags': getattr(e, 'tags', None),
        'is_flagged': bool(getattr(e, 'is_flagged', False)),
        'asset_ids': war_room_timelines_biz.event_asset_ids(e.id),
        'ioc_ids': war_room_timelines_biz.event_ioc_ids(e.id),
        'created_by_id': e.created_by_id,
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None
