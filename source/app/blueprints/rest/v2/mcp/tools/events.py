#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""MCP tools for the case-scoped timeline (events) domain."""
from __future__ import annotations

import dateutil.parser
from marshmallow import ValidationError

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    PAGINATION_SCHEMA_FRAGMENT,
    VIEW_SCHEMA_FRAGMENT,
    VIEW_FULL,
    build_pagination,
    clip_text_field,
    requested_view,
)
from app.business.events import events_create
from app.business.events import events_delete
from app.business.events import events_get
from app.business.events import events_get_assets_ids
from app.business.events import events_get_iocs_ids
from app.business.events import events_list_filtered
from app.business.events import events_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import EventSchema


_event_schema = EventSchema()

# The format `EventSchema` reads `event_date` in
_EVENT_DATE_FORMAT = '%Y-%m-%dT%H:%M:%S.%f'

# Fields `EventSchema` loads, for the callers (documentation only: the
# schema validates the payload).
_EVENT_PAYLOAD_PROPERTIES: dict[str, dict] = {
    'event_title': {'type': 'string', 'description': 'Short title, 2 characters at least.'},
    'event_date': {
        'type': 'string',
        'description': 'When it happened, ISO 8601 (e.g. "2026-10-14T08:30:00"). '
                       'An offset in the date is used as `event_tz` when that is not given.',
    },
    'event_tz': {'type': 'string', 'description': 'UTC offset of `event_date`, e.g. "+02:00". Defaults to "+00:00".'},
    'event_category_id': {
        'type': 'integer',
        'description': 'Event category id: resolve the name with `iris_taxonomies_list` (event_categories).',
    },
    'event_content': {'type': 'string', 'description': 'Description of the event (markdown).'},
    'event_raw': {'type': 'string', 'description': 'Raw data the event comes from, e.g. a log line.'},
    'event_source': {'type': 'string', 'description': 'Where the event was observed.'},
    'event_tags': {'type': 'string', 'description': 'Comma-separated tags.'},
    'event_assets': {
        'type': 'array', 'items': {'type': 'integer'},
        'description': 'Ids of the case assets involved; replaces the current links.',
    },
    'event_iocs': {
        'type': 'array', 'items': {'type': 'integer'},
        'description': 'Ids of the case IOCs involved; replaces the current links.',
    },
    'event_sync_iocs_assets': {
        'type': 'boolean',
        'description': 'Also link the given IOCs to the given assets.',
    },
    'event_in_summary': {'type': 'boolean', 'description': 'Show the event in the case summary.'},
    'event_in_graph': {'type': 'boolean', 'description': 'Show the event in the case graph.'},
    'event_is_flagged': {'type': 'boolean', 'description': 'Flag the event.'},
}


# Event descriptions can hold whole log extracts: a preview per row,
# the full text with `view=full`
_EVENT_LIST_PREVIEWS = {'event_content': 400}


def _events_row(event: dict) -> dict:
    return {
        'event_id': event.get('event_id'),
        'event_date': event.get('event_date'),
        'event_tz': event.get('event_tz'),
        'event_title': event.get('event_title'),
        'category_name': event.get('category_name'),
        'event_tags': event.get('event_tags'),
        'event_content': event.get('event_content'),
        'event_in_summary': bool(event.get('event_in_summary')),
        'event_is_flagged': bool(event.get('event_is_flagged')),
        'parent_event_id': event.get('parent_event_id'),
        'assets': [{'asset_id': asset.get('id'), 'asset_name': asset.get('asset_name'),
                    'asset_type': asset.get('asset_type'), 'compromised': bool(asset.get('compromised'))}
                   for asset in event.get('assets') or []],
        'iocs': [{'ioc_id': ioc.get('id'), 'ioc_value': ioc.get('ioc_value')} for ioc in event.get('iocs') or []],
    }


@mcp_tool(
    name='iris_case_events_list',
    description=(
        'List the timeline events of a case, oldest first, with their category, '
        'linked assets and IOCs. `in_summary` keeps only the events flagged to '
        'appear in the case summary. Descriptions are clipped to a preview '
        'unless `view` is full.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            **VIEW_SCHEMA_FRAGMENT,
            'in_summary': {
                'type': 'boolean',
                'description': 'Only the events shown in the case summary.',
            },
        },
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_events_list(args: dict) -> dict:
    pagination = build_pagination(args)
    page = max(1, pagination.get_page())
    per_page = max(1, pagination.get_per_page())
    # Paged here rather than by events_list_filtered, which grafts the
    # parents and children of a page into it: walking the pages would
    # return those events several times
    events = events_list_filtered(args['case_identifier'], {}, page=1, per_page=0)['tim']
    # Ties on the date broken by id, so that the pages do not overlap
    events = sorted(events, key=lambda event: (str(event.get('event_date') or ''), event.get('event_id') or 0))
    if args.get('in_summary'):
        events = [event for event in events if event.get('event_in_summary')]
    rows = [_events_row(event) for event in events[(page - 1) * per_page:page * per_page]]
    if requested_view(args) != VIEW_FULL:
        for field, limit in _EVENT_LIST_PREVIEWS.items():
            clip_text_field(rows, field, limit)
    last_page = max(1, (len(events) + per_page - 1) // per_page)
    return {
        'total': len(events),
        'data': rows,
        'last_page': last_page,
        'current_page': page,
        'next_page': page + 1 if page < last_page else None,
    }


def _normalised_event_payload(payload: dict) -> dict:
    """Payload with `event_date` in the format the schema reads and an
    `event_tz`: the model rarely writes the microseconds the schema
    insists on."""
    payload = dict(payload)
    payload.setdefault('event_assets', [])
    payload.setdefault('event_iocs', [])
    if payload.get('event_date'):
        try:
            date = dateutil.parser.isoparse(str(payload['event_date']))
        except ValueError as exc:
            raise MCPError(protocol.INVALID_PARAMS,
                           f'Invalid event_date {payload["event_date"]!r}: expected ISO 8601.') from exc
        offset = date.strftime('%z')
        if offset and not payload.get('event_tz'):
            payload['event_tz'] = f'{offset[:3]}:{offset[3:]}'
        payload['event_date'] = date.replace(tzinfo=None).strftime(_EVENT_DATE_FORMAT)
    if not payload.get('event_tz'):
        payload['event_tz'] = '+00:00'
    return payload


def _has_offset(event_date) -> bool:
    try:
        return dateutil.parser.isoparse(str(event_date)).tzinfo is not None
    except ValueError:
        return False


def _load_event(payload: dict, **kwargs):
    event = _event_schema.load(payload, **kwargs)
    event.event_date, event.event_date_wtz = _event_schema.validate_date(payload['event_date'], payload['event_tz'])
    return event


def _get_event_in_case(event_id: int, case_id: int):
    event = events_get(event_id)
    if event.case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Event #{event_id} does not belong to case #{case_id}.')
    return event


def _event_result(event) -> dict:
    result = _event_schema.dump(event)
    result['event_assets'] = events_get_assets_ids(event)
    result['event_iocs'] = events_get_iocs_ids(event)
    return result


@mcp_tool(
    name='iris_case_events_create',
    description=(
        'Add an event to the timeline of a case. Required: `event_title`, '
        '`event_date` and `event_category_id` — resolve the category name '
        'with `iris_taxonomies_list` first. Assets and IOCs are linked by '
        'their ids in the case.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Event fields.',
                'properties': _EVENT_PAYLOAD_PROPERTIES,
                'required': ['event_title', 'event_date', 'event_category_id'],
            },
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_events_create(args: dict) -> dict:
    payload = _normalised_event_payload(args['payload'])
    try:
        event = _load_event(payload)
        event = events_create(args['case_identifier'], event, payload.get('event_category_id'),
                              payload['event_assets'], payload['event_iocs'],
                              payload.get('event_sync_iocs_assets', False))
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _event_result(event)


@mcp_tool(
    name='iris_case_events_update',
    description=(
        'Update an event of the timeline of a case (partial payload '
        'accepted: the fields not given are kept). `event_assets` and '
        '`event_iocs`, when given, replace the current links.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'event_identifier': {'type': 'integer'},
            'payload': {
                'type': 'object',
                'description': 'Event fields to change; the others are kept.',
                'properties': _EVENT_PAYLOAD_PROPERTIES,
            },
        },
        'required': ['event_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_events_update(args: dict) -> dict:
    try:
        event = _get_event_in_case(args['event_identifier'], args['case_identifier'])
        # The schema wants the whole event: complete the payload with
        # the current values
        given = args['payload']
        current = {
            'event_title': event.event_title,
            'event_category_id': _event_schema.get_event_category_id(event),
            'event_assets': events_get_assets_ids(event),
            'event_iocs': events_get_iocs_ids(event),
        }
        if 'event_date' not in given:
            current['event_date'] = (event.event_date_wtz or event.event_date).strftime(_EVENT_DATE_FORMAT)
        if 'event_date' not in given or not _has_offset(given['event_date']):
            current['event_tz'] = event.event_tz
        payload = _normalised_event_payload({**current, **given, 'event_id': event.event_id})
        event = _load_event(payload, instance=event)
        event = events_update(event, payload.get('event_category_id'), payload['event_assets'],
                              payload['event_iocs'], payload.get('event_sync_iocs_assets', False))
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Event not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _event_result(event)


@mcp_tool(
    name='iris_case_events_delete',
    description='Delete an event from the timeline of a case.',
    input_schema={
        'type': 'object',
        'properties': {'event_identifier': {'type': 'integer'}},
        'required': ['event_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_events_delete(args: dict) -> dict:
    try:
        event = _get_event_in_case(args['event_identifier'], args['case_identifier'])
        events_delete(iris_current_user, event)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Event not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'deleted': True, 'event_id': args['event_identifier']}
