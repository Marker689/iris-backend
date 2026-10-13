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

"""MCP tools for the case-scoped evidences domain."""
from __future__ import annotations

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
from app.business.evidences import evidences_filter
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.pagination_parameters import PaginationParameters
from app.schema.marshables import CaseEvidenceSchema


_evidence_schema = CaseEvidenceSchema()

# Default projection for `iris_case_evidences_list`: drops the chain of
# custody, custom attributes and modification history
_EVIDENCE_SUMMARY_FIELDS = (
    'id',
    'filename',
    'file_hash',
    'file_size',
    'file_description',
    'date_added',
    'acquisition_date',
    'start_date',
    'end_date',
    'type_id',
    'type.name',
    'user.user_name',
)

_evidence_summary_schema = CaseEvidenceSchema(only=_EVIDENCE_SUMMARY_FIELDS)

_EVIDENCE_LIST_PREVIEWS = {'file_description': 400}


@mcp_tool(
    name='iris_case_evidences_list',
    description=(
        'List the evidences of a case: the files and data collected during the '
        'investigation, with their hash, size, type and dates. Oldest first unless '
        '`order_by` / `sort_dir` say otherwise.'
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
def iris_case_evidences_list(args: dict) -> dict:
    pagination = build_pagination(args)
    if not pagination.get_order_by():
        pagination = PaginationParameters(pagination.get_page(), pagination.get_per_page(), 'date_added',
                                          pagination.get_direction())
    try:
        result = evidences_filter(args['case_identifier'], pagination)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    schema = schema_for_view(args, _evidence_summary_schema, _evidence_schema)
    return dump_paginated(schema, result, previews=_EVIDENCE_LIST_PREVIEWS)
