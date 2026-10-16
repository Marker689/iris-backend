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

"""Who may follow the live changes of a war room, and the fan-out of a
case change to the war rooms the case is attached to. The pushes
themselves are in `app.iris_engine.war_rooms_live`."""

import logging

from app.business.war_rooms import war_room_exists
from app.business.war_rooms import war_rooms_for_case
from app.business.war_rooms_access import ac_fast_check_user_has_war_room_access
from app.datamgmt.manage.manage_users_db import get_user
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.war_rooms_live import war_rooms_live_emit
from app.iris_engine.war_rooms_live import war_rooms_live_room
from app.models.authorization import Permissions
from app.models.authorization import WarRoomAccessLevel
from app.models.authorization import ac_flag_match_mask
from app.models.errors import ObjectNotFoundError


logger = logging.getLogger(__name__)


def war_rooms_live_join_room(user_id, war_room_id) -> str:
    """The Socket.IO room of a war room the user may read, as the REST
    views check it; raises ObjectNotFoundError otherwise."""
    if isinstance(war_room_id, bool) or not isinstance(war_room_id, int) or war_room_id <= 0:
        raise ObjectNotFoundError()
    user = get_user(user_id) if user_id else None
    if user is None or not user.active or not war_room_exists(war_room_id):
        raise ObjectNotFoundError()
    permissions = ac_get_effective_permissions_of_user(user)
    if not ac_flag_match_mask(permissions, Permissions.server_administrator.value) \
            and not permissions & Permissions.war_rooms_read.value:
        raise ObjectNotFoundError()
    if ac_fast_check_user_has_war_room_access(
            user_id, war_room_id, [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access]) is None:
        raise ObjectNotFoundError()
    return war_rooms_live_room(war_room_id)


def war_rooms_live_notify_case(case_id, topic, actor_id=None):
    """Push `topic` to every war room the case is attached to; never
    raises."""
    try:
        war_room_ids = {row.war_room_id for row in war_rooms_for_case(int(case_id))}
    except Exception:
        logger.exception('Could not list the war rooms of case %s', case_id)
        return
    for war_room_id in sorted(war_room_ids):
        war_rooms_live_emit(war_room_id, topic, actor_id=actor_id)
