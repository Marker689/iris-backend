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

"""MCP tools for the war-room scope: the assets and IOCs of the attached
cases, the Assets Manager (registry) assets attached to the room, and
the staging area.

Same authorization as the REST routes in `war_rooms/scope.py`: dispatch
checks the war-room access, then every tool computes the attached cases
the caller can read and, for writes, the subset it has `full_access` on.
The business layer only ever sees these explicit case-id lists, so the
objects of a case the caller cannot read are never listed, copied or
touched. Discarding a staged object and detaching a registry asset stay
in the UI, like the other deletions.
"""
from __future__ import annotations

from app.blueprints.access_controls import ac_current_user_can_read_vulnerabilities
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.access_controls import ac_current_user_permissions_mask
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.war_rooms.access import war_room_readable_attached_case_ids
from app.business.managed_assets import managed_assets_viewer_scope
from app.business.war_room_managed_assets import war_room_managed_assets_attach
from app.business.war_room_managed_assets import war_room_managed_assets_list
from app.business.war_room_scope import war_room_scope_bulk_flag
from app.business.war_room_scope import war_room_scope_bulk_status
from app.business.war_room_scope import war_room_scope_create_asset
from app.business.war_room_scope import war_room_scope_create_ioc
from app.business.war_room_scope import war_room_scope_list_assets
from app.business.war_room_scope import war_room_scope_list_iocs
from app.business.war_room_scope import war_room_scope_push_assets
from app.business.war_room_scope import war_room_scope_push_iocs
from app.business.war_room_scope import war_room_scope_serialize_staged
from app.business.war_room_scope import war_room_scope_staged_create
from app.business.war_room_scope import war_room_scope_staged_list
from app.business.war_room_scope import war_room_scope_staged_push
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_READ = (Permissions.war_rooms_read, Permissions.server_administrator)
_WRITE = (Permissions.war_rooms_write, Permissions.server_administrator)

# A page, not the capped full list: the result goes into the LLM context.
_DEFAULT_PER_PAGE = 50

_PAGE_PROPERTIES = {
    'page': {'type': 'integer', 'minimum': 1, 'description': 'Page number (1-based, default 1).'},
    'per_page': {'type': 'integer', 'minimum': 1, 'maximum': 200,
                 'description': f'Page size (default {_DEFAULT_PER_PAGE}).'},
}

_CASE_IDS = {
    'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1,
    'description': 'Target attached cases; you need full access on each (iris_war_room_cases_list).',
}

_ASSET_PAYLOAD = {
    'type': 'object',
    'properties': {
        'asset_name': {'type': 'string', 'minLength': 1},
        'asset_type_id': {'type': 'integer', 'description': 'asset_types of iris_taxonomies_list.'},
        'asset_description': {'type': 'string'},
        'asset_ip': {'type': 'string'},
        'asset_domain': {'type': 'string'},
        'asset_tags': {'type': 'string', 'description': 'Comma-separated tags.'},
        'asset_compromise_status_id': {'type': 'integer'},
        'analysis_status_id': {'type': 'integer'},
    },
    'required': ['asset_name', 'asset_type_id'],
}

_IOC_PAYLOAD = {
    'type': 'object',
    'properties': {
        'ioc_value': {'type': 'string', 'minLength': 1},
        'ioc_type_id': {'type': 'integer', 'description': 'ioc_types of iris_taxonomies_list.'},
        'ioc_tlp_id': {'type': 'integer'},
        'ioc_description': {'type': 'string'},
        'ioc_tags': {'type': 'string', 'description': 'Comma-separated tags.'},
    },
    'required': ['ioc_value', 'ioc_type_id'],
}


def _writable_case_ids(readable_case_ids):
    granted = ac_fast_check_current_user_has_cases_access(readable_case_ids, [CaseAccessLevel.full_access])
    return [case_id for case_id in readable_case_ids if case_id in granted]


def _user():
    return iris_current_user._get_current_object()  # type: ignore[attr-defined]


def _run(fn, *fn_args, **fn_kwargs):
    try:
        return fn(*fn_args, **fn_kwargs)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc


def _require_registry_access():
    if not ac_current_user_has_permission(Permissions.asset_manager_read):
        raise MCPError(protocol.IRIS_ACCESS_DENIED, 'Registry assets need the asset_manager_read permission.')


def _registry_scope():
    return managed_assets_viewer_scope(_user(), ac_current_user_permissions_mask())


# ---- Listing -------------------------------------------------------

@mcp_tool(
    name='iris_war_room_scope_assets_list',
    description=(
        'List the assets of the cases attached to the war room (only the cases you can read), '
        'one page at a time, with the totals per case and per flag. Filters: q (substring of '
        'the name, IP, domain, description or tags), case_id, flag_id (or "none" for the '
        'unflagged ones), without_flag_id, compromised, vulnerable (open, exploited or none), '
        'vulnerability (identifier or alias). Flags are in iris_taxonomies_list.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'q': {'type': 'string'},
            'case_id': {'type': 'integer'},
            'flag_id': {'type': 'string'},
            'without_flag_id': {'type': 'integer'},
            'compromised': {'type': 'boolean'},
            'vulnerable': {'type': 'string', 'enum': ['open', 'exploited', 'none']},
            'vulnerability': {'type': 'string'},
            'sort': {'type': 'string', 'enum': ['name', 'case']},
            **_PAGE_PROPERTIES,
        },
    },
    permissions=_READ,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_assets_list(args: dict) -> dict:
    return _run(
        war_room_scope_list_assets,
        war_room_readable_attached_case_ids(args['war_room_id']),
        search=args.get('q'),
        flag=args.get('flag_id'),
        without_flag=args.get('without_flag_id'),
        case_id=args.get('case_id'),
        compromised=args.get('compromised'),
        vulnerable=args.get('vulnerable'),
        vulnerability=args.get('vulnerability'),
        page=args.get('page') or 1,
        per_page=args.get('per_page') or _DEFAULT_PER_PAGE,
        sort=args.get('sort'),
        include_vulnerabilities=ac_current_user_can_read_vulnerabilities(),
    )


@mcp_tool(
    name='iris_war_room_scope_iocs_list',
    description=(
        'List the IOCs of the cases attached to the war room (only the cases you can read), '
        'one page of distinct indicators at a time, each with the cases it is seen in. '
        'Filters: q (substring of the value, description or tags), case_id. sort=spread puts '
        'the indicators seen in the most cases first.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'q': {'type': 'string'},
            'case_id': {'type': 'integer'},
            'sort': {'type': 'string', 'enum': ['value', 'spread']},
            **_PAGE_PROPERTIES,
        },
    },
    permissions=_READ,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_iocs_list(args: dict) -> dict:
    return _run(
        war_room_scope_list_iocs,
        war_room_readable_attached_case_ids(args['war_room_id']),
        search=args.get('q'),
        case_id=args.get('case_id'),
        page=args.get('page') or 1,
        per_page=args.get('per_page') or _DEFAULT_PER_PAGE,
        sort=args.get('sort'),
    )


@mcp_tool(
    name='iris_war_room_scope_registry_assets_list',
    description=(
        'List the Assets Manager (registry) assets attached to the war room, with their '
        'optional case and vulnerability links. Needs the asset_manager_read permission: '
        'without it the list is empty and allowed is false.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=_READ,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_registry_assets_list(args: dict) -> dict:
    if not ac_current_user_has_permission(Permissions.asset_manager_read):
        return {'data': [], 'truncated': False, 'limit': 0, 'allowed': False}
    war_room_id = args['war_room_id']
    result = war_room_managed_assets_list(war_room_id, _registry_scope(),
                                          war_room_readable_attached_case_ids(war_room_id),
                                          ac_current_user_can_read_vulnerabilities())
    result['allowed'] = True
    return result


@mcp_tool(
    name='iris_war_room_scope_staging_list',
    description=(
        'List the assets and IOCs staged in the war room: drafted there, not yet created in '
        'any case. Push one with iris_war_room_scope_staging_push.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=_READ,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_staging_list(args: dict) -> dict:
    return {'staged': war_room_scope_staged_list(args['war_room_id'])}


# ---- Create / push into cases --------------------------------------

@mcp_tool(
    name='iris_war_room_scope_assets_create',
    description=(
        'Create an asset in one or more attached cases at once. flag_ids optionally sets '
        'asset flags on every copy (flag_reason when a flag requires a reason). The result '
        'lists, per target case, the created asset or why it was refused.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'asset': _ASSET_PAYLOAD,
            'case_ids': _CASE_IDS,
            'flag_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'flag_reason': {'type': 'string'},
        },
        'required': ['asset', 'case_ids'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_assets_create(args: dict) -> dict:
    war_room_id = args['war_room_id']
    writable = _writable_case_ids(war_room_readable_attached_case_ids(war_room_id))
    return _run(war_room_scope_create_asset, war_room_id, _user(), args.get('asset'), args.get('case_ids'),
                writable, flag_ids=args.get('flag_ids'), flag_reason=args.get('flag_reason'))


@mcp_tool(
    name='iris_war_room_scope_iocs_create',
    description=(
        'Create an IOC in one or more attached cases at once. The result lists, per target '
        'case, the created IOC or why it was refused.'
    ),
    input_schema={
        'type': 'object',
        'properties': {'ioc': _IOC_PAYLOAD, 'case_ids': _CASE_IDS},
        'required': ['ioc', 'case_ids'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_iocs_create(args: dict) -> dict:
    war_room_id = args['war_room_id']
    writable = _writable_case_ids(war_room_readable_attached_case_ids(war_room_id))
    return _run(war_room_scope_create_ioc, war_room_id, _user(), args.get('ioc'), args.get('case_ids'), writable)


@mcp_tool(
    name='iris_war_room_scope_assets_push',
    description=(
        'Copy assets of attached cases (asset_id from iris_war_room_scope_assets_list) into '
        'other attached cases. A case that already holds the asset is skipped.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'asset_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
            'case_ids': _CASE_IDS,
        },
        'required': ['asset_ids', 'case_ids'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_assets_push(args: dict) -> dict:
    war_room_id = args['war_room_id']
    readable = war_room_readable_attached_case_ids(war_room_id)
    return _run(war_room_scope_push_assets, war_room_id, _user(), args.get('asset_ids'), args.get('case_ids'),
                readable, _writable_case_ids(readable))


@mcp_tool(
    name='iris_war_room_scope_iocs_push',
    description=(
        'Copy IOCs of attached cases (ioc_id from iris_war_room_scope_iocs_list) into other '
        'attached cases. A case that already holds the IOC is skipped.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'ioc_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
            'case_ids': _CASE_IDS,
        },
        'required': ['ioc_ids', 'case_ids'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_iocs_push(args: dict) -> dict:
    war_room_id = args['war_room_id']
    readable = war_room_readable_attached_case_ids(war_room_id)
    return _run(war_room_scope_push_iocs, war_room_id, _user(), args.get('ioc_ids'), args.get('case_ids'),
                readable, _writable_case_ids(readable))


@mcp_tool(
    name='iris_war_room_scope_assets_flag',
    description=(
        'Set (action=set, default) or remove (action=clear) an asset flag on several assets of '
        'the attached cases. flag_id comes from the asset_flags of iris_taxonomies_list; a flag '
        'with requires_reason needs a reason, one with requires_decision a decision_id '
        '(iris_war_room_decisions_list). The result tells which assets were changed.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'asset_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
            'flag_id': {'type': 'integer'},
            'action': {'type': 'string', 'enum': ['set', 'clear']},
            'reason': {'type': 'string'},
            'decision_id': {'type': 'integer'},
        },
        'required': ['asset_ids', 'flag_id'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_assets_flag(args: dict) -> dict:
    war_room_id = args['war_room_id']
    readable = war_room_readable_attached_case_ids(war_room_id)
    return _run(war_room_scope_bulk_flag, war_room_id, _user().id, args.get('asset_ids'), args.get('flag_id'),
                args.get('action') or 'set', args.get('reason'), args.get('decision_id'),
                readable, _writable_case_ids(readable))


@mcp_tool(
    name='iris_war_room_scope_assets_status',
    description=(
        'Set the compromise status and / or the analysis status of several assets of the '
        'attached cases (ids from iris_taxonomies_list).'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'asset_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
            'asset_compromise_status_id': {'type': 'integer'},
            'analysis_status_id': {'type': 'integer'},
        },
        'required': ['asset_ids'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_assets_status(args: dict) -> dict:
    war_room_id = args['war_room_id']
    readable = war_room_readable_attached_case_ids(war_room_id)
    return _run(war_room_scope_bulk_status, war_room_id, args.get('asset_ids'),
                args.get('asset_compromise_status_id'), args.get('analysis_status_id'),
                readable, _writable_case_ids(readable))


@mcp_tool(
    name='iris_war_room_scope_registry_assets_attach',
    description=(
        'Attach Assets Manager (registry) assets to the war room, optionally linked to an '
        'attached case and / or a catalogue vulnerability. Needs the asset_manager_read '
        'permission.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'managed_asset_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
            'case_id': {'type': 'integer'},
            'vulnerability_id': {'type': 'integer'},
            'note': {'type': 'string'},
        },
        'required': ['managed_asset_ids'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_registry_assets_attach(args: dict) -> dict:
    _require_registry_access()
    war_room_id = args['war_room_id']
    body = {k: args[k] for k in ('managed_asset_ids', 'case_id', 'vulnerability_id', 'note') if k in args}
    return _run(war_room_managed_assets_attach, war_room_id, body, _user().id, _registry_scope(),
                war_room_readable_attached_case_ids(war_room_id), ac_current_user_can_read_vulnerabilities())


# ---- Staging area --------------------------------------------------

@mcp_tool(
    name='iris_war_room_scope_staging_create',
    description=(
        'Stage an asset or an IOC in the war room without creating it in any case yet, e.g. '
        'to let the analysts review it. proposed_case_ids are the cases it is meant for.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'object_type': {'type': 'string', 'enum': ['asset', 'ioc']},
            'payload': {
                'type': 'object',
                'description': ('The asset (asset_name, asset_type_id, ...) or IOC (ioc_value, '
                                'ioc_type_id, ...), with the fields of the create tools.'),
            },
            'proposed_case_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'note': {'type': 'string'},
        },
        'required': ['object_type', 'payload'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_staging_create(args: dict) -> dict:
    data = {k: args[k] for k in ('object_type', 'payload', 'proposed_case_ids', 'note') if k in args}
    staged = _run(war_room_scope_staged_create, args['war_room_id'], _user().id, data)
    return war_room_scope_serialize_staged(staged)


@mcp_tool(
    name='iris_war_room_scope_staging_push',
    description=(
        'Create a staged asset or IOC (id from iris_war_room_scope_staging_list) in attached '
        'cases: case_ids, or its proposed cases when omitted.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'staged_id': {'type': 'integer'},
            'case_ids': _CASE_IDS,
        },
        'required': ['staged_id'],
    },
    permissions=_WRITE,
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_scope_staging_push(args: dict) -> dict:
    war_room_id = args['war_room_id']
    writable = _writable_case_ids(war_room_readable_attached_case_ids(war_room_id))
    return _run(war_room_scope_staged_push, war_room_id, _user(), args['staged_id'], args.get('case_ids'),
                writable)
