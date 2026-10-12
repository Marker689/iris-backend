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

"""MCP tools for the vulnerability catalogue and findings.

Same visibility as the REST routes: catalogue counts and exposure cover
the cases the caller can read (and the registry when they hold
`asset_manager_read`), the war-room matrix only the attached cases they
can read, plus the entries the room tracks (which a war-room writer can
add). Catalogue entries and case findings can be created, changed and
deleted, under the rules of their REST routes; merging entries stays in
the UI / REST API.

These tools serve the MCP server and the in-app LLM alike, so their
results leave the instance: like case export, they never carry the
content of a private catalogue entry (IRIS-VULN-…), only its identifier,
scoring and where it is found (`withheld: true`). Private entries are
not matched on their content either (title, tags, aliases).
"""
from __future__ import annotations

from app.blueprints.access_controls import ac_current_user_can_create_vulnerabilities
from app.blueprints.access_controls import ac_current_user_can_read_vulnerabilities
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.manage_routes.vulnerabilities import vulnerabilities_can_edit
from app.blueprints.rest.v2.manage_routes.vulnerabilities import vulnerabilities_visibility
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import PAGINATION_SCHEMA_FRAGMENT
from app.blueprints.rest.v2.mcp.tools._common import build_pagination
from app.business.vulnerabilities import vulnerabilities_create
from app.business.vulnerabilities import vulnerabilities_delete
from app.business.vulnerabilities import vulnerabilities_exposure
from app.business.vulnerabilities import vulnerabilities_get
from app.business.vulnerabilities import vulnerabilities_get_public
from app.business.vulnerabilities import vulnerabilities_lookup
from app.business.vulnerabilities import vulnerabilities_mask_private
from app.business.vulnerabilities import vulnerabilities_search
from app.business.vulnerabilities import vulnerabilities_update
from app.business.vulnerability_findings import vulnerability_findings_case_create
from app.business.vulnerability_findings import vulnerability_findings_case_delete
from app.business.vulnerability_findings import vulnerability_findings_case_list
from app.business.vulnerability_findings import vulnerability_findings_case_update
from app.business.war_room_scope import war_room_scope_attached_case_ids
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_matrix
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_track
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.vulnerabilities import FINDING_EXPLOITATION_STATUSES
from app.models.vulnerabilities import FINDING_NOT_AFFECTED_JUSTIFICATIONS
from app.models.vulnerabilities import FINDING_REMEDIATION_STATUSES
from app.models.vulnerabilities import VULNERABILITY_KINDS
from app.models.vulnerabilities import VULNERABILITY_SEVERITIES

_SEVERITY_LIST = {
    'type': 'array',
    'items': {'type': 'string', 'enum': ['critical', 'high', 'medium', 'low', 'none', 'unknown']},
    'description': 'Only these severities.',
}


def _require(create=False):
    allowed = ac_current_user_can_create_vulnerabilities() if create else ac_current_user_can_read_vulnerabilities()
    if not allowed:
        needed = 'vulnerabilities_create' if create else 'vulnerabilities_read'
        raise MCPError(protocol.IRIS_ACCESS_DENIED, f'This tool needs the {needed} permission.')


def _run(operation, not_found='Vulnerability not found.'):
    try:
        return vulnerabilities_mask_private(operation())
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, not_found) from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc


@mcp_tool(
    name='iris_vulnerabilities_search',
    description=(
        'Search the vulnerability catalogue (CVEs, advisories, private IRIS-VULN entries). Each row '
        'carries its severity, CVSS, KEV flag and how many findings the caller can see (open, fixed, '
        'exploited). The content of private entries is withheld (withheld: true); they only match '
        'on their identifier.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            'search': {'type': 'string', 'description': 'Substring of the identifier, an alias, the title or tags.'},
            'severity': _SEVERITY_LIST,
            'kev': {'type': 'boolean', 'description': 'Only (or no) CISA KEV entries.'},
            'affected': {'type': 'boolean',
                         'description': 'Only entries with an open finding in a readable case.'},
        },
    },
    permissions=(Permissions.vulnerabilities_read, Permissions.server_administrator),
    mvp=True,
)
def iris_vulnerabilities_search(args: dict) -> dict:
    return _run(lambda: vulnerabilities_search(args, build_pagination(args), *vulnerabilities_visibility(),
                                               hide_private_content=True))


@mcp_tool(
    name='iris_vulnerabilities_get',
    description=(
        'Fetch one catalogue entry, by numeric id or by identifier / alias (e.g. CVE-2024-3400), '
        'with the cases and registry assets it affects. The content of a private entry is withheld; '
        'it is only found by its own identifier.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'vulnerability_id': {'type': 'integer'},
            'identifier': {'type': 'string'},
        },
    },
    permissions=(Permissions.vulnerabilities_read, Permissions.server_administrator),
    mvp=True,
)
def iris_vulnerabilities_get(args: dict) -> dict:
    def _get():
        case_ids, registry_scope = vulnerabilities_visibility()
        if args.get('vulnerability_id') is not None:
            vulnerability = vulnerabilities_get(args['vulnerability_id'])
            entry = vulnerabilities_get_public(vulnerability, case_ids, registry_scope)
        elif args.get('identifier'):
            entry = vulnerabilities_lookup(args['identifier'], case_ids, registry_scope, hide_private_content=True)
            if entry is None:
                raise ObjectNotFoundError()
            vulnerability = vulnerabilities_get(entry['vulnerability_id'])
        else:
            raise BusinessProcessingError('vulnerability_id or identifier is required')
        entry['exposure'] = vulnerabilities_exposure(vulnerability, case_ids, registry_scope)
        return entry

    return _run(_get)


def _body(args: dict, fields) -> dict:
    # Only the keys the caller set: the business layer tells "absent" from null
    return {key: args[key] for key in fields if key in args}


_CATALOGUE_FIELDS = ('identifier', 'is_private', 'title', 'description', 'kind', 'severity', 'cvss_vector',
                     'cvss_score', 'cwes', 'tags', 'reference_urls', 'aliases')
_CATALOGUE_UPDATE_FIELDS = tuple(field for field in _CATALOGUE_FIELDS if field != 'is_private')

_CATALOGUE_PROPERTIES = {
    'identifier': {'type': 'string', 'description': 'Public identifier; omit for a private entry.'},
    'is_private': {'type': 'boolean', 'description': 'Private entry with an allocated identifier.'},
    'title': {'type': 'string'},
    'description': {'type': 'string'},
    'kind': {'type': 'string', 'enum': list(VULNERABILITY_KINDS)},
    'severity': {'type': 'string', 'enum': list(VULNERABILITY_SEVERITIES)},
    'cvss_vector': {'type': 'string', 'description': 'CVSS 2.0 to 4.0 vector; sets the version and score.'},
    'cvss_score': {'type': 'number', 'description': '0 to 10.'},
    'cwes': {'type': 'array', 'items': {'type': 'string'}, 'description': 'e.g. ["CWE-79"].'},
    'tags': {'type': 'array', 'items': {'type': 'string'}},
    'reference_urls': {'type': 'array', 'items': {'type': 'string'}, 'description': 'http(s) URLs.'},
    'aliases': {'type': 'array', 'items': {'type': 'string'},
                'description': 'Other identifiers of the same vulnerability.'},
}


@mcp_tool(
    name='iris_vulnerabilities_create',
    description=(
        'Add an entry to the vulnerability catalogue. A public entry names itself (CVE-…, GHSA-…, a '
        'vendor advisory id) and must not be in the catalogue yet; a private one (is_private, for an '
        'internal finding with no public identifier) gets the next IRIS-VULN-<year>-NNNN identifier. '
        'Severity is derived from the CVSS vector or score unless given. To record a vulnerability on '
        'assets of a case, use iris_case_vulnerabilities_create, which also adds unknown public '
        'identifiers on the fly.'
    ),
    input_schema={
        'type': 'object',
        'properties': _CATALOGUE_PROPERTIES,
        'required': ['title'],
    },
    permissions=(Permissions.vulnerabilities_create, Permissions.server_administrator),
    mvp=True,
)
def iris_vulnerabilities_create(args: dict) -> dict:
    _require(create=True)
    user_id = iris_current_user._get_current_object().id  # type: ignore[attr-defined]
    return _run(lambda: vulnerabilities_get_public(vulnerabilities_create(_body(args, _CATALOGUE_FIELDS), user_id),
                                                   *vulnerabilities_visibility()))


@mcp_tool(
    name='iris_vulnerabilities_update',
    description=(
        'Change a catalogue entry; fields left out keep their value, and a given list (cwes, tags, '
        'reference_urls, aliases) replaces the previous one. Allowed on the entries you created, or '
        'on any entry with the vulnerabilities_write permission. A public entry may be renamed to '
        'another free identifier; a private one keeps its own, and is_private never changes.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'vulnerability_id': {'type': 'integer', 'description': 'Catalogue id of the entry.'},
            **{field: _CATALOGUE_PROPERTIES[field] for field in _CATALOGUE_UPDATE_FIELDS},
        },
        'required': ['vulnerability_id'],
    },
    permissions=(Permissions.vulnerabilities_create, Permissions.vulnerabilities_write,
                 Permissions.server_administrator),
    mvp=True,
)
def iris_vulnerabilities_update(args: dict) -> dict:
    _require()
    user_id = iris_current_user._get_current_object().id  # type: ignore[attr-defined]

    def _update():
        vulnerability = vulnerabilities_get(args['vulnerability_id'])
        if not vulnerabilities_can_edit(vulnerability):
            raise MCPError(protocol.IRIS_ACCESS_DENIED,
                           'You may only change the catalogue entries you created, unless you hold the '
                           'vulnerabilities_write permission.')
        vulnerability = vulnerabilities_update(vulnerability, _body(args, _CATALOGUE_UPDATE_FIELDS), user_id)
        return vulnerabilities_get_public(vulnerability, *vulnerabilities_visibility())

    return _run(_update)


@mcp_tool(
    name='iris_vulnerabilities_delete',
    description=(
        'Delete a catalogue entry that no finding uses (an entry still recorded on assets is refused: '
        'merge it into another one in the UI instead). Server administrators only.'
    ),
    input_schema={
        'type': 'object',
        'properties': {'vulnerability_id': {'type': 'integer', 'description': 'Catalogue id of the entry.'}},
        'required': ['vulnerability_id'],
    },
    permissions=(Permissions.server_administrator,),
)
def iris_vulnerabilities_delete(args: dict) -> dict:
    _require()

    def _delete():
        vulnerability = vulnerabilities_get(args['vulnerability_id'])
        identifier = vulnerability.identifier
        vulnerabilities_delete(vulnerability)
        return {'deleted': True, 'vulnerability_id': args['vulnerability_id'], 'identifier': identifier}

    return _run(_delete)


@mcp_tool(
    name='iris_case_vulnerabilities_list',
    description=(
        'List the vulnerability findings of a case, worst first: asset, remediation status '
        '(affected, patched, mitigated, risk-accepted…), exploitation status, due date, evidence.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'status_group': {'type': 'string', 'enum': ['open', 'fixed', 'dismissed']},
            'severity': _SEVERITY_LIST,
            'exploitation': {
                'type': 'array',
                'items': {'type': 'string',
                          'enum': ['unknown', 'not-exploited', 'attempted', 'suspected', 'exploited']},
            },
            'asset_id': {'type': 'integer', 'description': 'Only the findings of this asset.'},
            'overdue': {'type': 'boolean', 'description': 'Only open findings past their due date.'},
            'search': {'type': 'string'},
        },
    },
    permissions=(Permissions.vulnerabilities_read, Permissions.server_administrator),
    case_scoped=True,
    mvp=True,
)
def iris_case_vulnerabilities_list(args: dict) -> dict:
    return _run(lambda: vulnerability_findings_case_list(args['case_identifier'], args))


_FINDING_FIELDS = ('asset_ids', 'vulnerability_id', 'identifier', 'title', 'remediation_status',
                   'exploitation_status', 'status_reason', 'not_affected_justification', 'component',
                   'installed_version', 'fixed_version', 'detection_source', 'notes', 'due_date', 'ioc_ids',
                   'event_ids')
_FINDING_PROPERTIES = {
    'remediation_status': {'type': 'string', 'enum': list(FINDING_REMEDIATION_STATUSES)},
    'exploitation_status': {'type': 'string', 'enum': list(FINDING_EXPLOITATION_STATUSES)},
    'status_reason': {'type': 'string'},
    'not_affected_justification': {'type': 'string', 'enum': list(FINDING_NOT_AFFECTED_JUSTIFICATIONS)},
    'component': {'type': 'string', 'description': 'Affected package, service or component.'},
    'installed_version': {'type': 'string'},
    'fixed_version': {'type': 'string'},
    'detection_source': {'type': 'string', 'description': 'Scanner, EDR, manual review…'},
    'notes': {'type': 'string'},
    'due_date': {'type': 'string', 'description': 'Remediation due date, YYYY-MM-DD.'},
    'ioc_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'IOCs of the case as evidence.'},
    'event_ids': {'type': 'array', 'items': {'type': 'integer'},
                  'description': 'Timeline events of the case as evidence.'},
}
# What a finding is about (its asset and vulnerability) is fixed once recorded
_FINDING_UPDATE_FIELDS = tuple(_FINDING_PROPERTIES)


@mcp_tool(
    name='iris_case_vulnerabilities_create',
    description=(
        'Record a vulnerability on one or more assets of the case, one finding per asset (assets that '
        'already carry it are skipped and reported). Identify it by catalogue vulnerability_id, or by '
        'identifier (CVE, GHSA…): an unknown public identifier is added to the catalogue, with title '
        'when given. The remediation status defaults to under-analysis; risk-accepted and '
        'false-positive need a status_reason, not-affected a not_affected_justification or a reason.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'asset_ids': {'type': 'array', 'items': {'type': 'integer'},
                          'description': 'Assets of the case the vulnerability is found on.'},
            'vulnerability_id': {'type': 'integer', 'description': 'Catalogue id.'},
            'identifier': {'type': 'string', 'description': 'CVE / advisory identifier or alias.'},
            'title': {'type': 'string', 'description': 'Title of a catalogue entry added on the fly.'},
            **_FINDING_PROPERTIES,
        },
        'required': ['asset_ids'],
    },
    permissions=(Permissions.vulnerabilities_create, Permissions.server_administrator),
    case_scoped=True,
    mvp=True,
)
def iris_case_vulnerabilities_create(args: dict) -> dict:
    _require(create=True)
    user_id = iris_current_user._get_current_object().id  # type: ignore[attr-defined]
    return _run(lambda: vulnerability_findings_case_create(args['case_identifier'],
                                                           _body(args, _FINDING_FIELDS), user_id))


@mcp_tool(
    name='iris_case_vulnerabilities_update',
    description=(
        'Change a vulnerability finding of the case (finding_id from iris_case_vulnerabilities_list): '
        'remediation / exploitation status, versions, due date, notes, evidence. Fields left out keep '
        'their value; ioc_ids and event_ids replace the previous evidence. Its asset and '
        'vulnerability cannot change. risk-accepted and false-positive need a status_reason, '
        'not-affected a not_affected_justification or a reason. The change is kept in the history '
        'of the finding.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'finding_id': {'type': 'integer', 'description': 'Numeric ID of the finding.'},
            **_FINDING_PROPERTIES,
        },
        'required': ['finding_id'],
    },
    permissions=(Permissions.vulnerabilities_create, Permissions.server_administrator),
    case_scoped=True,
    mvp=True,
)
def iris_case_vulnerabilities_update(args: dict) -> dict:
    _require(create=True)
    user_id = iris_current_user._get_current_object().id  # type: ignore[attr-defined]
    return _run(lambda: vulnerability_findings_case_update(args['case_identifier'], args['finding_id'],
                                                           _body(args, _FINDING_UPDATE_FIELDS), user_id),
                not_found='Finding not found in this case.')


@mcp_tool(
    name='iris_case_vulnerabilities_delete',
    description=(
        'Delete a vulnerability finding of the case (finding_id from iris_case_vulnerabilities_list), '
        'with its history. To record that the asset is no longer affected, prefer '
        'iris_case_vulnerabilities_update with a remediation status such as patched or not-affected.'
    ),
    input_schema={
        'type': 'object',
        'properties': {'finding_id': {'type': 'integer', 'description': 'Numeric ID of the finding.'}},
        'required': ['finding_id'],
    },
    permissions=(Permissions.vulnerabilities_create, Permissions.server_administrator),
    case_scoped=True,
    mvp=True,
)
def iris_case_vulnerabilities_delete(args: dict) -> dict:
    _require(create=True)

    def _delete():
        vulnerability_findings_case_delete(args['case_identifier'], args['finding_id'])
        return {'deleted': True, 'finding_id': args['finding_id']}

    return _run(_delete, not_found='Finding not found in this case.')


@mcp_tool(
    name='iris_war_room_vulnerabilities_matrix',
    description=(
        'One page of the vulnerability x case matrix of a war room (attached cases the caller can read, '
        'plus the entries the room tracks): per entry, per case, the findings open / fixed / dismissed / '
        'exploited, exploited-and-open first. `total` is the number of entries over all pages.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'page': {'type': 'integer', 'description': '1-indexed page number.'},
            'per_page': {'type': 'integer', 'description': 'Entries per page (default 50, max 200).'},
            'search': {'type': 'string', 'description': 'Substring of the identifier or title.'},
            'case_id': {'type': 'integer', 'description': 'Only the entries found in this attached case.'},
            'tracked': {'type': 'boolean', 'description': 'Only the entries the war room tracks.'},
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_vulnerabilities_matrix(args: dict) -> dict:
    _require()
    attached = war_room_scope_attached_case_ids(args['war_room_id'])
    granted = ac_fast_check_current_user_has_cases_access(
        attached, [CaseAccessLevel.read_only, CaseAccessLevel.full_access])
    readable = [case_id for case_id in attached if case_id in granted]
    query = {key: args.get(key) for key in ('page', 'per_page', 'search', 'case_id', 'tracked')}
    return _run(lambda: war_room_vulnerabilities_matrix(args['war_room_id'], readable, query,
                                                        hide_private_content=True))


@mcp_tool(
    name='iris_war_room_vulnerabilities_track',
    description=(
        'Track a vulnerability on a war room, even when no asset is known to be affected yet. '
        'Identify it by catalogue vulnerability_id or by identifier (CVE, GHSA…; unknown public '
        'identifiers are added to the catalogue). Idempotent; a given note replaces the previous one.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'vulnerability_id': {'type': 'integer'},
            'identifier': {'type': 'string'},
            'note': {'type': 'string'},
        },
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
)
def iris_war_room_vulnerabilities_track(args: dict) -> dict:
    _require(create=True)
    body = {key: args.get(key) for key in ('vulnerability_id', 'identifier', 'note')}
    return _run(lambda: war_room_vulnerabilities_track(args['war_room_id'], body, iris_current_user._get_current_object().id)[0])  # type: ignore[attr-defined]
