#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the Alerts domain."""
from __future__ import annotations

from app.blueprints.access_controls import (
    ac_current_user_has_customer_access,
    ac_current_user_permissions_mask,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    VIEW_SCHEMA_FRAGMENT,
    clip_text_field,
    schema_for_view,
)
from app.business.access_controls import check_ua_case_client
from app.business.alerts import (
    alerts_create,
    alerts_escalate,
    alerts_get,
    alerts_get_related,
    alerts_merge,
    alerts_search,
    alerts_update,
)
from app.models.alerts import AlertResolutionStatus, AlertStatus, Severity
from app.models.authorization import Permissions
from app.models.cases import CaseClassification
from app.business.customers import customers_get
from app.models.errors import BusinessProcessingError, ObjectNotFoundError, SearchQueryError
from app.schema.marshables import AlertSchema, CaseAssetsSchema, IocSchema
from marshmallow.exceptions import ValidationError

import copy


_alert_schema = AlertSchema()
_ioc_schema = IocSchema()
_asset_schema = CaseAssetsSchema()

# Default projection for `iris_alerts_list`. Everything a triage pass
# reasons over — ids to act on, names so no taxonomy lookup is needed
# first, and the IOC / asset *values* rather than their full records.
#
# Deliberately absent: `alert_source_content` and `alert_context` (the
# raw detection payloads, routinely several KB each), `modification_history`,
# `comments`, `custom_attributes`. Those are what make an unprojected
# page of alerts cost tens of thousands of tokens, and none of them are
# needed to decide which alert to open. `view=full` or
# `iris_alerts_get` still return them.
_ALERT_SUMMARY_FIELDS = (
    'alert_id',
    'alert_title',
    'alert_description',
    'alert_source',
    'alert_source_ref',
    'alert_source_event_time',
    'alert_creation_time',
    'alert_tags',
    'alert_severity_id',
    'alert_status_id',
    'alert_resolution_status_id',
    'alert_classification_id',
    'alert_customer_id',
    'alert_owner_id',
    'severity.severity_name',
    'status.status_name',
    'resolution_status.resolution_status_name',
    'classification.name',
    'customer.customer_name',
    'owner.id',
    'owner.user_name',
    'iocs.ioc_id',
    'iocs.ioc_value',
    'iocs.ioc_type_id',
    'assets.asset_id',
    'assets.asset_name',
    'assets.asset_type_id',
    'cases',
    'clusters',
)

_alert_summary_schema = AlertSchema(only=_ALERT_SUMMARY_FIELDS)

# Long free-text clipped to a preview in list responses: enough to tell
# two alerts apart, not enough to matter times a page of rows.
#
# Sized against the response budget rather than picked by eye. The rest
# of a projected alert row is ~1.2 kB, so at the default page of 25 the
# description is what decides whether the page fits inside
# `result_budget.MAX_RESULT_BYTES`. At 300 a full page lands comfortably
# under it; at 600 it does not, and every ordinary list call would come
# back carrying a truncation notice. Read a description in full with
# `iris_alerts_get`.
_ALERT_LIST_PREVIEWS = {'alert_description': 300}

# What `fields` may narrow a page to: the summary plus the lifecycle
# timestamps. Meant for callers that page through many alerts to count
# them (a weekly false-positive review), where a few fields per row let
# a full page of 100 fit the response budget.
_ALERT_SELECTABLE_FIELDS = _ALERT_SUMMARY_FIELDS + ('date_update', 'resolved_at')
_ALERT_FIELDS_SCHEMAS: dict[tuple, AlertSchema] = {}


def _alert_fields_schema(fields) -> AlertSchema | None:
    """The projection on `fields` (a list or a comma-separated string),
    or None when no field is asked for."""
    if isinstance(fields, str):
        fields = fields.split(',')
    if not isinstance(fields, list):
        raise MCPError(protocol.INVALID_PARAMS, '`fields` must be a list of field names.')
    names = {str(f).strip() for f in fields if str(f).strip()}
    if not names:
        return None
    unknown = sorted(names - set(_ALERT_SELECTABLE_FIELDS))
    if unknown:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Unknown field(s) {", ".join(unknown)}; choose from {", ".join(_ALERT_SELECTABLE_FIELDS)}.')
    key = tuple(sorted(names | {'alert_id'}))
    if key not in _ALERT_FIELDS_SCHEMAS:
        _ALERT_FIELDS_SCHEMAS[key] = AlertSchema(only=key)
    return _ALERT_FIELDS_SCHEMAS[key]


# Fields the LLM is allowed to update via iris_alerts_update. Kept in
# lock-step with the REST endpoint's _ALERT_READONLY_UPDATE_FIELDS
# (alert_id / alert_customer_id / alert_creation_time are immutable —
# see GHSA-8hwq-v6vm-9grr / SBA-ADV-20260128-05 / CWE-863).
_IOC_IMPORT_LIST = {
    'type': 'array',
    'items': {'type': 'string'},
    'description': 'UUIDs (`ioc_uuid`) of the alert IOCs to copy into the case; none when omitted.',
}
_ASSET_IMPORT_LIST = {
    'type': 'array',
    'items': {'type': 'string'},
    'description': 'UUIDs (`asset_uuid`) of the alert assets to copy into the case; none when omitted.',
}

_ALERT_UPDATE_PROPERTIES: dict[str, dict] = {
    'alert_title':                 {'type': 'string'},
    'alert_description':           {'type': 'string'},
    'alert_source':                {'type': 'string'},
    'alert_source_ref':            {'type': 'string'},
    'alert_source_link':           {'type': 'string'},
    'alert_source_event_time':     {'type': 'string', 'description': 'ISO-8601 datetime.'},
    'alert_note':                  {'type': 'string', 'description': 'Free-form analyst note.'},
    'alert_tags':                  {'type': 'string', 'description': 'Comma-separated tags.'},
    'alert_severity_id':           {'type': 'integer'},
    'alert_status_id':             {'type': 'integer'},
    'alert_resolution_status_id':  {'type': ['integer', 'null']},
    'alert_classification_id':     {'type': ['integer', 'null']},
    'alert_owner_id':              {
        'type': ['integer', 'null'],
        'description': 'User id to assign as owner. Use -1 or null to unassign.',
    },
    'alert_context':               {'type': 'object'},
}


# Fields iris_alerts_create accepts. The customer is a top-level argument
# (checked before anything is written, and pinned to the customers of an
# AI workflow run); ids, timestamps and history are never caller-set.
_ALERT_CREATE_PROPERTIES: dict[str, dict] = {
    **_ALERT_UPDATE_PROPERTIES,
    'alert_source_content':        {'type': 'object', 'description': 'Raw event from the source.'},
}
_ALERT_IOC_PROPERTIES: dict[str, dict] = {
    'ioc_value':       {'type': 'string'},
    'ioc_type_id':     {'type': 'integer', 'description': 'IOC type id (see the manage IOC types).'},
    'ioc_tlp_id':      {'type': 'integer', 'description': 'TLP id: 1 red, 2 amber, 3 green, 4 clear, 5 amber+strict.'},
    'ioc_description': {'type': 'string'},
    'ioc_tags':        {'type': 'string', 'description': 'Comma-separated tags.'},
    'ioc_enrichment':  {'type': 'object'},
}
_ALERT_ASSET_PROPERTIES: dict[str, dict] = {
    'asset_name':        {'type': 'string'},
    'asset_type_id':     {'type': 'integer', 'description': 'Asset type id (see the manage asset types).'},
    'asset_description': {'type': 'string'},
    'asset_domain':      {'type': 'string'},
    'asset_ip':          {'type': 'string'},
    'asset_info':        {'type': 'string'},
    'asset_tags':        {'type': 'string', 'description': 'Comma-separated tags.'},
    'asset_enrichment':  {'type': 'object'},
}
_ALERT_CREATE_MAX_ITEMS = 500


def _only_known_fields(name: str, value, allowed: dict) -> dict:
    """`value` as a dict, refusing keys outside `allowed`: an id would make
    the schema load (and change) an existing row."""
    if not isinstance(value, dict):
        raise MCPError(protocol.INVALID_PARAMS, f'`{name}` must be an object.')
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'`{name}`: unknown field(s) {", ".join(unknown)}; allowed: {", ".join(allowed)}.')
    return dict(value)


def _load_alert_items(name: str, items, allowed: dict, schema) -> list:
    if items is None:
        return []
    if not isinstance(items, list):
        raise MCPError(protocol.INVALID_PARAMS, f'`{name}` must be a list.')
    if len(items) > _ALERT_CREATE_MAX_ITEMS:
        raise MCPError(protocol.INVALID_PARAMS, f'`{name}` holds at most {_ALERT_CREATE_MAX_ITEMS} items.')
    rows = [_only_known_fields(f'{name}.{index}', item, allowed) for index, item in enumerate(items)]
    try:
        return schema.load(rows, many=True, partial=True)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error in `{name}`: {exc.messages}') from exc


def _default_alert_status_id() -> int:
    status = AlertStatus.query.filter(AlertStatus.status_name == 'New').first()
    if status is None:
        raise MCPError(protocol.INVALID_PARAMS,
                       'No "New" alert status: set `alert_status_id` (see iris_alerts_status_list).')
    return status.status_id


def _get_alert(alert_id: int):
    try:
        return alerts_get(
            iris_current_user,
            ac_current_user_permissions_mask(),
            alert_id,
            fallback_customer_access=ac_current_user_has_customer_access,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Alert #{alert_id} not found or access denied.') from exc


@mcp_tool(
    name='iris_alerts_list',
    description=(
        'Search alerts visible to the caller. All filters are optional; '
        'omit them to page through every alert the caller has access to. '
        'Prefer the `query` expression over the scalar filters: it says in '
        'one string what most of them say together, and it resolves names '
        'so no id lookup is needed first. Rows come back as triage '
        'summaries; use `iris_alerts_get` for one alert in full, including '
        'its raw source payload.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **VIEW_SCHEMA_FRAGMENT,
            'query': {
                'type': 'string',
                'description': (
                    'Lucene-style search expression, ANDed with the other '
                    'filters. Values are names, not ids: '
                    '`status:Closed severity:>=High`, `owner:me`, '
                    '`is:open is:unassigned`, `asset:*.corp.local`, '
                    '`created:>now-24h`, `source:crowdstrike OR source:sentinel`, '
                    '`context.rule_name:"brute force"`, `-status:Merged`. '
                    'Supports AND / OR / NOT (or `-`), parentheses, quoted '
                    'phrases, ranges `[a TO b]`, and `*` / `?` wildcards. A '
                    'bare word searches title, description, source, '
                    'reference, tags and note. `is:` takes open, closed, '
                    'assigned, unassigned, escalated, merged, resolved, '
                    'unresolved, clustered, orphan, in_case, no_case. A '
                    'malformed expression is rejected with the offset of '
                    'the problem, so it can be corrected and retried.'
                ),
            },
            'page': {'type': 'integer'},
            'per_page': {'type': 'integer'},
            'sort': {'type': 'string', 'description': 'Field to sort by, prefixed with - for descending.'},
            'title': {'type': 'string'},
            'description': {'type': 'string'},
            'status': {'type': 'string'},
            'severity': {'type': 'integer'},
            'owner': {'type': 'integer'},
            'source': {'type': 'string'},
            'tags': {'type': 'string'},
            'case_identifier': {'type': 'integer'},
            'customer_identifier': {'type': 'integer'},
            'classification': {'type': 'integer'},
            'alert_identifiers': {'type': 'string', 'description': 'Comma-separated list of IDs.'},
            'assets': {'type': 'string'},
            'iocs': {'type': 'string'},
            'resolution_status': {'type': 'integer'},
            'source_reference': {'type': 'string'},
            'start_date': {'type': 'string'},
            'end_date': {'type': 'string'},
            'source_start_date': {'type': 'string'},
            'source_end_date': {'type': 'string'},
            'fields': {
                'type': 'array',
                'items': {'type': 'string', 'enum': list(_ALERT_SELECTABLE_FIELDS)},
                'description': (
                    'Return only these fields of each alert (plus `alert_id`), '
                    'instead of the `view`. For counting over many alerts: a '
                    'few fields per row fit a page of 100. `resolved_at` and '
                    '`date_update` are only available here.'
                ),
            },
        },
    },
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_list(args: dict) -> dict:
    page = int(args.get('page') or 1)
    per_page = int(args.get('per_page') or DEFAULT_PER_PAGE)
    if per_page > MAX_PER_PAGE:
        per_page = MAX_PER_PAGE
    sort = args.get('sort') or 'desc'
    fields_schema = _alert_fields_schema(args.get('fields') or [])

    try:
        result = alerts_search(
            start_date=args.get('start_date'),
            end_date=args.get('end_date'),
            source_start_date=args.get('source_start_date'),
            source_end_date=args.get('source_end_date'),
            title=args.get('title'),
            description=args.get('description'),
            status=args.get('status'),
            severity=args.get('severity'),
            owner=args.get('owner'),
            source=args.get('source'),
            tags=args.get('tags'),
            case_identifier=args.get('case_identifier'),
            customer_identifier=args.get('customer_identifier'),
            classification=args.get('classification'),
            alert_identifiers=args.get('alert_identifiers'),
            assets=args.get('assets'),
            iocs=args.get('iocs'),
            resolution_status=args.get('resolution_status'),
            source_reference=args.get('source_reference'),
            custom_conditions=None,
            user_identifier_filter=iris_current_user.id,
            page=page,
            per_page=per_page,
            sort=sort,
            query=args.get('query'),
            query_user_identifier=iris_current_user.id,
        )
    except SearchQueryError as exc:
        # INVALID_PARAMS rather than the INTERNAL_ERROR the dispatcher
        # would produce: a bad expression is the caller's to fix, and the
        # offset tells it where to look.
        position = exc.get_position()
        where = f' at character {position}' if position is not None else ''
        raise MCPError(
            protocol.INVALID_PARAMS, f'{exc.get_message()}{where}'
        ) from exc

    schema = fields_schema or schema_for_view(args, _alert_summary_schema, _alert_schema)
    rows = schema.dump(result.items, many=True)
    for field, limit in _ALERT_LIST_PREVIEWS.items():
        clip_text_field(rows, field, limit)

    return {
        'total': result.total,
        'data': rows,
        'last_page': result.pages,
        'current_page': result.page,
        'next_page': result.next_num if result.has_next else None,
    }


@mcp_tool(
    name='iris_alerts_get',
    description='Fetch a single alert by ID.',
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
        },
        'required': ['alert_identifier'],
    },
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_get(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    return _alert_schema.dump(alert)


@mcp_tool(
    name='iris_alerts_escalate',
    description=(
        'Escalate an alert to a brand-new case. Optionally imports IOCs '
        'and assets from the alert into the case.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'iocs_import_list': _IOC_IMPORT_LIST,
            'assets_import_list': _ASSET_IMPORT_LIST,
            'note': {'type': 'string'},
            'import_as_event': {'type': 'boolean'},
            'case_tags': {'type': 'string'},
            'case_title': {'type': 'string'},
            'case_template_id': {'type': 'integer'},
        },
        'required': ['alert_identifier'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_escalate(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    try:
        case = alerts_escalate(
            alert,
            iocs_import_list=args.get('iocs_import_list'),
            assets_import_list=args.get('assets_import_list'),
            note=args.get('note'),
            import_as_event=bool(args.get('import_as_event')),
            case_tags=args.get('case_tags'),
            case_title=args.get('case_title'),
            case_template_id=args.get('case_template_id'),
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'case_id': case.case_id, 'case_name': case.name}


@mcp_tool(
    name='iris_alerts_merge',
    description='Merge an alert into an existing case.',
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'target_case_id': {'type': 'integer'},
            'iocs_import_list': _IOC_IMPORT_LIST,
            'assets_import_list': _ASSET_IMPORT_LIST,
            'note': {'type': 'string'},
            'import_as_event': {'type': 'boolean'},
            'case_tags': {'type': 'string'},
        },
        'required': ['alert_identifier', 'target_case_id'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_merge(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    target_case_id = args['target_case_id']
    # Same entitlement REST's merge_alert enforces: being allowed to read
    # the alert says nothing about the *target* case, so without this an
    # alert could be walked into a case belonging to another customer.
    if not check_ua_case_client(iris_current_user.id, target_case_id):
        raise MCPError(
            protocol.IRIS_ACCESS_DENIED,
            f'Not entitled to merge alerts into case #{target_case_id}.',
        )
    try:
        case = alerts_merge(
            alert,
            target_case_id,
            iocs_import_list=args.get('iocs_import_list'),
            assets_import_list=args.get('assets_import_list'),
            note=args.get('note'),
            import_as_event=bool(args.get('import_as_event')),
            case_tags=args.get('case_tags'),
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Target case not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'case_id': case.case_id, 'case_name': case.name}


@mcp_tool(
    name='iris_alerts_related_get',
    description='Return the related-alerts graph payload for an alert.',
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'open_alerts': {'type': 'boolean'},
            'closed_alerts': {'type': 'boolean'},
            'open_cases': {'type': 'boolean'},
            'closed_cases': {'type': 'boolean'},
            'days_back': {'type': 'integer'},
            'number_of_nodes': {'type': 'integer'},
        },
        'required': ['alert_identifier'],
    },
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_related_get(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    days_back = int(args.get('days_back') or 180)
    if days_back < 0:
        days_back = 180
    number_of_results = int(args.get('number_of_nodes') or 100)
    if number_of_results < 0:
        number_of_results = 100
    return alerts_get_related(
        iris_current_user,
        alert,
        bool(args.get('open_alerts')),
        bool(args.get('closed_alerts')),
        bool(args.get('open_cases', True)),
        bool(args.get('closed_cases', True)),
        days_back,
        number_of_results,
    )


@mcp_tool(
    name='iris_alerts_update',
    description=(
        'Update an alert. Payload accepts partial fields — omit fields you do not want to '
        'change. Common uses: assign an owner (`alert_owner_id`), set the workflow status '
        '(`alert_status_id`), record a resolution (`alert_resolution_status_id`), change '
        'severity (`alert_severity_id`), classify (`alert_classification_id`), add tags '
        '(`alert_tags`), append an analyst note (`alert_note`). Use the corresponding '
        '`iris_alerts_*_list` tools to discover the numeric ids first. Pass '
        '`alert_owner_id: -1` (or null) to unassign an owner.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'payload': {
                'type': 'object',
                'description': 'Partial alert fields to update.',
                'properties': _ALERT_UPDATE_PROPERTIES,
                'additionalProperties': False,
            },
        },
        'required': ['alert_identifier', 'payload'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_update(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    payload = dict(args.get('payload') or {})
    if not payload:
        raise MCPError(protocol.INVALID_PARAMS, 'Payload must contain at least one field.')

    pristine_alert = copy.copy(alert)

    # Mirror the REST route's "unassign owner" convention so the LLM
    # sees a single consistent semantics for clearing an assignee.
    if payload.get('alert_owner_id') in (-1, '-1'):
        payload['alert_owner_id'] = None

    try:
        updated_alert = _alert_schema.load(payload, instance=alert, partial=True)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc

    activity_data = []
    for key, value in payload.items():
        old_value = getattr(pristine_alert, key, None)
        if key not in ('alert_content', 'alert_note'):
            activity_data.append(f'"{key}" from "{old_value}" to "{value}"')
        else:
            activity_data.append(f'"{key}"')

    try:
        result = alerts_update(pristine_alert, updated_alert, activity_data)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _alert_schema.dump(result)


@mcp_tool(
    name='iris_alerts_create',
    description=(
        'Create an alert for a customer, with optional IOCs and assets, as POST /api/v2/alerts does: '
        'the alert runs the alert-create hooks, rules and workflows like any ingested alert. '
        '`payload` needs `alert_title` and `alert_severity_id`; `alert_status_id` defaults to New. '
        'Use `iris_alerts_severity_list` / `iris_alerts_status_list` / `iris_alerts_classification_list` '
        'for the numeric ids, and `iris_customers_list` for the customer.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'alert_customer_id': {'type': 'integer', 'description': 'Customer the alert belongs to.'},
            'payload': {
                'type': 'object',
                'description': 'Alert fields.',
                'properties': _ALERT_CREATE_PROPERTIES,
                'required': ['alert_title', 'alert_severity_id'],
                'additionalProperties': False,
            },
            'alert_iocs': {
                'type': 'array',
                'description': 'IOCs of the alert: each needs `ioc_value`, `ioc_type_id` and `ioc_tlp_id`.',
                'items': {'type': 'object', 'properties': _ALERT_IOC_PROPERTIES, 'additionalProperties': False},
            },
            'alert_assets': {
                'type': 'array',
                'description': 'Assets of the alert: each needs `asset_name` and `asset_type_id`.',
                'items': {'type': 'object', 'properties': _ALERT_ASSET_PROPERTIES, 'additionalProperties': False},
            },
        },
        'required': ['alert_customer_id', 'payload'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_create(args: dict) -> dict:
    customer_id = args['alert_customer_id']
    # Checked before anything is written: the customer must exist and be
    # one the caller may create alerts for.
    try:
        customers_get(customer_id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Customer #{customer_id} not found or access denied.') from exc
    if not ac_current_user_has_customer_access(customer_id):
        raise MCPError(protocol.INVALID_PARAMS, f'Customer #{customer_id} not found or access denied.')

    payload = _only_known_fields('payload', args['payload'], _ALERT_CREATE_PROPERTIES)
    for key in ('alert_title', 'alert_severity_id'):
        if payload.get(key) in (None, ''):
            raise MCPError(protocol.INVALID_PARAMS, f'`payload.{key}` is required.')
    if Severity.query.filter(Severity.severity_id == payload['alert_severity_id']).first() is None:
        raise MCPError(protocol.INVALID_PARAMS, 'Unknown `alert_severity_id` (see iris_alerts_severity_list).')
    if payload.get('alert_status_id') is None:
        payload['alert_status_id'] = _default_alert_status_id()
    elif AlertStatus.query.filter(AlertStatus.status_id == payload['alert_status_id']).first() is None:
        raise MCPError(protocol.INVALID_PARAMS, 'Unknown `alert_status_id` (see iris_alerts_status_list).')
    if payload.get('alert_owner_id') in (-1, '-1'):
        payload['alert_owner_id'] = None
    payload['alert_customer_id'] = customer_id

    iocs = _load_alert_items('alert_iocs', args.get('alert_iocs'), _ALERT_IOC_PROPERTIES, _ioc_schema)
    assets = _load_alert_items('alert_assets', args.get('alert_assets'), _ALERT_ASSET_PROPERTIES, _asset_schema)
    try:
        alert = _alert_schema.load(payload)
        result = alerts_create(alert, iocs, assets)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _alert_schema.dump(result)


@mcp_tool(
    name='iris_alerts_status_list',
    description=(
        'List the alert workflow statuses configured on this instance '
        '(e.g. New, Assigned, In progress, Closed). Returns the numeric '
        '`status_id` values usable in `iris_alerts_update.alert_status_id` '
        'and in `iris_alerts_list` filters.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_status_list(args: dict) -> dict:
    rows = AlertStatus.query.order_by(AlertStatus.status_id).all()
    return {
        'data': [
            {
                'status_id': r.status_id,
                'status_name': r.status_name,
                'status_description': r.status_description,
            }
            for r in rows
        ]
    }


@mcp_tool(
    name='iris_alerts_resolution_list',
    description=(
        'List the alert resolution statuses (e.g. True positive, False '
        'positive, Benign, Not applicable). Returns numeric '
        '`resolution_status_id` values usable in '
        '`iris_alerts_update.alert_resolution_status_id`.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_resolution_list(args: dict) -> dict:
    rows = AlertResolutionStatus.query.order_by(AlertResolutionStatus.resolution_status_id).all()
    return {
        'data': [
            {
                'resolution_status_id': r.resolution_status_id,
                'resolution_status_name': r.resolution_status_name,
                'resolution_status_description': r.resolution_status_description,
            }
            for r in rows
        ]
    }


@mcp_tool(
    name='iris_alerts_severity_list',
    description=(
        'List the severity levels configured on this instance (e.g. Low, '
        'Medium, High, Critical). Returns numeric `severity_id` values '
        'usable in `iris_alerts_update.alert_severity_id`.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_severity_list(args: dict) -> dict:
    rows = Severity.query.order_by(Severity.severity_id).all()
    return {
        'data': [
            {
                'severity_id': r.severity_id,
                'severity_name': r.severity_name,
                'severity_description': r.severity_description,
            }
            for r in rows
        ]
    }


@mcp_tool(
    name='iris_alerts_classification_list',
    description=(
        'List the alert / case classifications configured on this '
        'instance (MITRE-style categories). Returns numeric `id` values '
        'usable in `iris_alerts_update.alert_classification_id`.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_classification_list(args: dict) -> dict:
    rows = CaseClassification.query.order_by(CaseClassification.id).all()
    return {
        'data': [
            {
                'id': r.id,
                'name': r.name,
                'name_expanded': r.name_expanded,
                'description': r.description,
            }
            for r in rows
        ]
    }
