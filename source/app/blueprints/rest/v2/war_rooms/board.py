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

"""War-room board: cross-case asset status / decision overview.

The board shows the widgets the room's writers set (`PUT
/board/widgets`, war-room write access). The tracker widgets are
computed with the caller's tracker access (readable attached cases,
Assets Manager scope), so their counts and rows match what the caller
sees in the trackers tab."""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_current_user_can_read_vulnerabilities
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.blueprints.rest.v2.war_rooms.access import war_room_tracker_access
from app.business.war_room_board import war_room_board_attached_case_ids
from app.business.war_room_board import war_room_board_build
from app.business.war_room_board_widgets import war_room_board_widgets_get
from app.business.war_room_board_widgets import war_room_board_widgets_set
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_board_blueprint = Blueprint(
    'war_rooms_board_rest_v2', __name__,
    url_prefix='/<int:war_room_id>'
)


def _readable_case_ids(case_ids):
    return [
        case_id for case_id in case_ids
        if ac_fast_check_current_user_has_case_access(
            case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
        ) is not None
    ]


@war_rooms_board_blueprint.get('/board')
@ac_api_requires()
@api_doc(tags=['WarRoomBoard'],
         summary='Get the war room board (asset flags, decisions, tasks, trackers, attention list)')
def get_board(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    accessible = _readable_case_ids(war_room_board_attached_case_ids(war_room_id))
    return response_api_success(war_room_board_build(
        war_room_id, accessible, include_vulnerabilities=ac_current_user_can_read_vulnerabilities(),
        tracker_access=war_room_tracker_access(war_room_id)))


@war_rooms_board_blueprint.get('/board/widgets')
@ac_api_requires()
@api_doc(tags=['WarRoomBoard'], summary='Get the widgets of the war room board, without their data')
def get_board_widgets(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    widgets, customized = war_room_board_widgets_get(war_room_id)
    return response_api_success({'widgets': widgets, 'board_customized': customized})


@war_rooms_board_blueprint.put('/board/widgets')
@ac_api_requires()
@api_doc(tags=['WarRoomBoard'],
         summary='Set the widgets of the war room board for everyone (null = back to the default board)')
def put_board_widgets(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        widgets, customized = war_room_board_widgets_set(war_room_id, request.get_json(silent=True))
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_success({'widgets': widgets, 'board_customized': customized})
