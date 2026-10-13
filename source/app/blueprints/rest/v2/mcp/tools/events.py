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

from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    PAGINATION_SCHEMA_FRAGMENT,
    VIEW_SCHEMA_FRAGMENT,
    VIEW_FULL,
    build_pagination,
    clip_text_field,
    requested_view,
)
from app.business.events import events_list_filtered
from app.models.authorization import Permissions


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
