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

"""MCP tools for the case datastore: writing a text file (a report, an
export) into it. Binary uploads stay in the UI / REST API."""
from __future__ import annotations

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business.datastore import DATASTORE_CREATE_FILE_MAX_BYTES
from app.business.datastore import datastore_create_file
from app.iris_engine.demo_builder import DEMO_MODE_UPLOAD_MAX_BYTES
from app.iris_engine.demo_builder import demo_mode_upload_cap_message
from app.iris_engine.demo_builder import is_demo_mode_enabled
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.schema.marshables import DSFileSchema


_file_schema = DSFileSchema(exclude=('file_content',))


@mcp_tool(
    name='iris_case_datastore_files_create',
    description=(
        'Write a text file (Markdown, CSV, JSON, plain text...) into the case '
        'datastore, UTF-8 encoded. `folder` puts it in that folder of the '
        'datastore root, created when missing; without it the file goes to the '
        'root. A new file is created on every call, even with the same name.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'filename': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 255,
                'description': 'File name, extension included (e.g. report.md).',
            },
            'content': {
                'type': 'string',
                'description': f'The text of the file, at most {DATASTORE_CREATE_FILE_MAX_BYTES} bytes once encoded.',
            },
            'description': {'type': 'string', 'description': 'Description shown in the datastore.'},
            'tags': {'type': 'string', 'description': 'Comma-separated tags.'},
            'folder': {
                'type': 'string',
                'maxLength': 255,
                'description': 'Folder of the datastore root to put the file in (e.g. Reports).',
            },
        },
        'required': ['filename', 'content'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_datastore_files_create(args: dict) -> dict:
    case_id = args['case_identifier']
    content = str(args.get('content') or '').encode('utf-8')
    if is_demo_mode_enabled() and len(content) > DEMO_MODE_UPLOAD_MAX_BYTES:
        raise MCPError(protocol.INVALID_PARAMS, demo_mode_upload_cap_message())
    try:
        dsf = datastore_create_file(case_id, iris_current_user.id, args.get('filename'), content,
                                    description=args.get('description') or '', tags=args.get('tags') or '',
                                    folder_name=args.get('folder') or None)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    track_activity(f'File "{dsf.file_original_name}" added to DS', caseid=case_id)
    data = _file_schema.dump(dsf)
    data['file_url'] = f'/api/v2/cases/{case_id}/datastore/files/{dsf.file_id}'
    return data
