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

"""MCP tools for the access of a case: who can see it, and changing it.

Authorization mirrors `/api/v2/cases/<id>/access/*` through
`cases_access_guards`: listing users needs read access to the case;
listing groups and changing access needs to be a server administrator,
or to hold `case_access_manage` with full access to the case. Nobody
changes their own access, and a non-administrator only grants users
(or groups whose members all) can see the customer of the case.

None of these tools is in the MVP set: an administrator opts in through
`ServerSettings.mcp_tool_allowlist`. AI workflows only run the two
writes from explicit `action` / `suggest` nodes, never from a model
(`ai_workflows.tools.AGENT_EXCLUDED_TOOLS`).
"""
from __future__ import annotations

from app.blueprints.rest.v2.cases_access_guards import cases_access_guards_can_manage
from app.blueprints.rest.v2.cases_access_guards import cases_access_guards_check_group
from app.blueprints.rest.v2.cases_access_guards import cases_access_guards_check_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business.cases import cases_get_by_identifier
from app.business.cases_access import cases_access_get_group
from app.business.cases_access import cases_access_get_user
from app.business.cases_access import cases_access_list_groups
from app.business.cases_access import cases_access_set_group
from app.business.cases_access import cases_access_set_user
from app.business.users import get_users_list_restricted_from_case
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_ACCESS_LEVEL_NAMES = tuple(level.name for level in CaseAccessLevel)

_ACCESS_LEVEL_SCHEMA = {
    'type': 'string',
    'enum': list(_ACCESS_LEVEL_NAMES),
    'description': ('full_access (read and write), read_only, or deny_all (explicitly '
                    'denied, overriding what the user would get from their customer).'),
}


def _level_name(value):
    if value is None or not CaseAccessLevel.has_value(value):
        return None
    return CaseAccessLevel(value).name


def _case(case_identifier):
    try:
        return cases_get_by_identifier(case_identifier)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc


def _managed_case(case_identifier):
    case = _case(case_identifier)
    if not cases_access_guards_can_manage(case_identifier):
        raise MCPError(protocol.IRIS_ACCESS_DENIED,
                       f'You may not change the access of case #{case_identifier}: it needs the case access '
                       'management permission and full access to the case.')
    return case


def _read_level(args: dict) -> int:
    name = args.get('access_level')
    if name not in _ACCESS_LEVEL_NAMES:
        raise MCPError(protocol.INVALID_PARAMS, f'access_level must be one of {", ".join(_ACCESS_LEVEL_NAMES)}.')
    return CaseAccessLevel[name].value


@mcp_tool(
    name='iris_case_access_list',
    description=(
        'List who can access the case: every user with an effective access level '
        '(from their own grants, their groups and their customer). When you may '
        'manage the access of the case, also lists every group with its explicit '
        'access level on it (null when it has none) — the ids to pass to '
        'iris_case_access_set_group.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    case_scoped=True,
)
def iris_case_access_list(args: dict) -> dict:
    case_identifier = args['case_identifier']
    case = _case(case_identifier)
    users = [{
        'user_id': user['user_id'],
        'user_name': user['user_name'],
        'user_login': user['user_login'],
        'user_active': user['user_active'],
        'access_level': _level_name(user['user_access_level']),
    } for user in get_users_list_restricted_from_case(case.case_id)]
    result = {
        'case_id': case.case_id,
        'users': users,
        'can_manage_access': cases_access_guards_can_manage(case_identifier),
    }
    if result['can_manage_access']:
        result['groups'] = [{
            'group_id': group['group_id'],
            'group_name': group['group_name'],
            'access_level': _level_name(group['access_level']),
        } for group in cases_access_list_groups(case)]
    return result


@mcp_tool(
    name='iris_case_access_set_user',
    description=(
        "Set one user's access level on the case. You cannot change your own access, "
        'and unless you are a server administrator the user must have access to the '
        'customer of the case.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'user_id': {'type': 'integer', 'description': 'Numeric ID of the user.'},
            'access_level': _ACCESS_LEVEL_SCHEMA,
        },
        'required': ['user_id', 'access_level'],
    },
    permissions=(Permissions.case_access_manage, Permissions.server_administrator),
    case_scoped=True,
)
def iris_case_access_set_user(args: dict) -> dict:
    case = _managed_case(args['case_identifier'])
    level = _read_level(args)
    try:
        user = cases_access_get_user(args['user_id'])
        cases_access_guards_check_user(case, user)
        level = cases_access_set_user(case, user, level)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'User not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return {'case_id': case.case_id, 'user_id': user.id, 'access_level': level.name}


@mcp_tool(
    name='iris_case_access_set_group',
    description=(
        "Set one group's access level on the case, for all its members. Unless you "
        'are a server administrator, every member must have access to the customer of '
        'the case and you cannot lower the access of a group you belong to. Group ids '
        'are listed by iris_case_access_list.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'group_id': {'type': 'integer', 'description': 'Numeric ID of the group.'},
            'access_level': _ACCESS_LEVEL_SCHEMA,
        },
        'required': ['group_id', 'access_level'],
    },
    permissions=(Permissions.case_access_manage, Permissions.server_administrator),
    case_scoped=True,
)
def iris_case_access_set_group(args: dict) -> dict:
    case = _managed_case(args['case_identifier'])
    level = _read_level(args)
    try:
        group = cases_access_get_group(args['group_id'])
        cases_access_guards_check_group(case, group, level)
        level = cases_access_set_group(case, group, level)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Group not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return {'case_id': case.case_id, 'group_id': group.group_id, 'access_level': level.name}
