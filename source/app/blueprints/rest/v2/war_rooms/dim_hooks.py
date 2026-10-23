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

"""v2 war-room DIM hook invoker.

`POST /api/v2/war-rooms/<id>/dim-hooks/invoke` queues the
`on_manual_trigger_war_room` hook against the war room — the "Run"
entries a module, a webhook or a manual AI workflow (`workflow_id`, run
as the current user) adds to the war-room topbar.

A war room is not a case object, hence a route of its own rather than a
`type: war_room` branch on `/api/v2/cases/<cid>/dim-hooks/invoke`. The
target is the war room of the URL, behind the same write check as the
other war-room mutations.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_current_api_key_scope_mask
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.business.dim_hooks import invoke_hook_for_war_room
from app.business.war_rooms import war_room_get
from app.models.errors import BusinessProcessingError


war_rooms_dim_hooks_blueprint = Blueprint(
    'war_rooms_dim_hooks_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/dim-hooks'
)


@war_rooms_dim_hooks_blueprint.post('/invoke')
@ac_api_requires()
@api_doc(tags=['WarRooms'], summary='Invoke a DIM hook against a war room')
def invoke_war_room_dim_hook(war_room_id):
    denied = require_war_room_write(war_room_id)
    if denied is not None:
        return denied

    payload = request.get_json(silent=True) or {}

    try:
        result = invoke_hook_for_war_room(
            war_room_get(war_room_id),
            hook_name=payload.get('hook_name'),
            hook_ui_name=payload.get('hook_ui_name'),
            module_name=payload.get('module_name'),
            webhook_id=payload.get('webhook_id'),
            workflow_id=payload.get('workflow_id'),
            workflow_trigger_id=payload.get('workflow_trigger_id'),
            user_id=iris_current_user.id,
            scope_mask=ac_current_api_key_scope_mask(),
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    body = {'queued': result.queued}
    if result.logs:
        body['logs'] = result.logs

    return response_api_success(data=body)
