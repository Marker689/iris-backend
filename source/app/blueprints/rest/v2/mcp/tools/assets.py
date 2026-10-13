#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the case-scoped Assets domain."""
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
    dump_paginated,
    schema_for_view,
)
from app.business.assets import (
    assets_create,
    assets_delete,
    assets_filter,
    assets_get,
    assets_update,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseAssetsSchema


_asset_schema = CaseAssetsSchema()

# Default projection for `iris_case_assets_list`. Drops the nested
# `alerts`, `iocs` and `ioc_links` collections — an asset seen in many
# alerts otherwise drags all of them into every row — along with
# `asset_enrichment`, `asset_info`, `modification_history` and
# `custom_attributes`.
_ASSET_SUMMARY_FIELDS = (
    'asset_id',
    'asset_name',
    'asset_description',
    'asset_domain',
    'asset_ip',
    'asset_tags',
    'asset_type_id',
    'analysis_status_id',
    'asset_compromise_status_id',
    'case_id',
    'asset_type.asset_name',
    'analysis_status.name',
)

_asset_summary_schema = CaseAssetsSchema(only=_ASSET_SUMMARY_FIELDS)

# Fields `CaseAssetsSchema` loads, for the callers (documentation only:
# the schema validates the payload).
_ASSET_PAYLOAD_PROPERTIES: dict[str, dict] = {
    'asset_name': {'type': 'string', 'description': 'Asset name (hostname, account, …); unique per type in a case.'},
    'asset_type_id': {'type': 'integer', 'description': 'Asset type id (`asset_types` of `iris_taxonomies_list`).'},
    'asset_description': {'type': 'string', 'description': 'Markdown accepted.'},
    'asset_domain': {'type': 'string'},
    'asset_ip': {'type': 'string'},
    'asset_info': {'type': 'string', 'description': 'Free-form additional information.'},
    'asset_tags': {'type': 'string', 'description': 'Comma-separated tags.'},
    'analysis_status_id': {
        'type': 'integer',
        'description': 'Analysis status id (`analysis_statuses` of `iris_taxonomies_list`).',
    },
    'asset_compromise_status_id': {
        'type': 'integer',
        'enum': [0, 1, 2, 3],
        'description': '0 to be determined, 1 compromised, 2 not compromised, 3 unknown.',
    },
    'custom_attributes': {'type': 'object', 'description': 'Custom attribute values, by tab then field name.'},
}


def _get_asset_in_case(asset_id: int, case_id: int):
    asset = assets_get(asset_id)
    if asset.case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Asset #{asset_id} does not belong to case #{case_id}.')
    return asset


@mcp_tool(
    name='iris_case_assets_list',
    description=(
        'List assets attached to a case. Rows come back as summaries; use '
        '`iris_case_assets_get` for one asset in full, including its linked '
        'IOCs and alerts.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            **VIEW_SCHEMA_FRAGMENT,
        },
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_list(args: dict) -> dict:
    try:
        result = assets_filter(args['case_identifier'], build_pagination(args), {})
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return dump_paginated(
        schema_for_view(args, _asset_summary_schema, _asset_schema), result,
    )


@mcp_tool(
    name='iris_case_assets_get',
    description='Fetch a single asset by ID.',
    input_schema={
        'type': 'object',
        'properties': {'asset_identifier': {'type': 'integer'}},
        'required': ['asset_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_get(args: dict) -> dict:
    try:
        asset = _get_asset_in_case(args['asset_identifier'], args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Asset not found.') from exc
    return _asset_schema.dump(asset)


@mcp_tool(
    name='iris_case_assets_create',
    description='Add a new asset to a case.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Asset fields.',
                'properties': _ASSET_PAYLOAD_PROPERTIES,
                'required': ['asset_name', 'asset_type_id'],
            },
            'ioc_links': {
                'type': 'array',
                'items': {'type': 'integer'},
                'description': 'Ids of IOCs of the case to link to the asset.',
            },
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_create(args: dict) -> dict:
    try:
        asset = _asset_schema.load(args['payload'])
        asset = assets_create(
            iris_current_user,
            args['case_identifier'],
            asset,
            args.get('ioc_links') or [],
        )
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _asset_schema.dump(asset)


@mcp_tool(
    name='iris_case_assets_update',
    description=(
        'Update an existing case asset (partial payload accepted). To set '
        '`analysis_status_id`, `asset_type_id`, or `asset_compromise_status_id`, '
        'first call `iris_taxonomies_list` to resolve the target name '
        '(e.g. "Done") to its numeric id — do NOT probe by trial-updating.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'asset_identifier': {'type': 'integer'},
            'payload': {
                'type': 'object',
                'description': 'Asset fields to change; the others are kept.',
                'properties': _ASSET_PAYLOAD_PROPERTIES,
            },
        },
        'required': ['asset_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_update(args: dict) -> dict:
    try:
        asset = _get_asset_in_case(args['asset_identifier'], args['case_identifier'])
        _asset_schema.load(args['payload'], instance=asset, partial=True)
        asset = assets_update(asset)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Asset not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _asset_schema.dump(asset)


@mcp_tool(
    name='iris_case_assets_delete',
    description='Delete an asset from a case.',
    input_schema={
        'type': 'object',
        'properties': {'asset_identifier': {'type': 'integer'}},
        'required': ['asset_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_delete(args: dict) -> dict:
    try:
        asset = _get_asset_in_case(args['asset_identifier'], args['case_identifier'])
        assets_delete(asset)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Asset not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'deleted': True, 'asset_id': args['asset_identifier']}
