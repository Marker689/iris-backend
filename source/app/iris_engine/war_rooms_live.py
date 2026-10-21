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

"""Live refresh of the war-room views, pushed over Socket.IO.

Every write to a war room (or to a case attached to it) sends
`war_room_changed {war_room_id, topic}` to the room's
`/notifications` room. Joining it checks the user may read the war room
(`war_rooms_live_join_room`). A push never carries data: the open views
of that topic re-read it over REST, with the reader's own access, so a
case the reader cannot see never leaks through a push. Nor does it say
who made the change: an attached case the reader cannot open would
otherwise still show who works on it, and when."""

import logging


logger = logging.getLogger(__name__)

LIVE_EVENT = 'war_room_changed'

TOPIC_ROOM = 'room'
TOPIC_MEMBERS = 'members'
TOPIC_CASES = 'cases'
TOPIC_TASKS = 'tasks'
TOPIC_SCOPE = 'scope'
TOPIC_VULNERABILITIES = 'vulnerabilities'
TOPIC_TIMELINES = 'timelines'
TOPIC_DECISIONS = 'decisions'
TOPIC_TEAMS = 'teams'
TOPIC_DATASTORE = 'datastore'
TOPIC_SITREPS = 'sitreps'
TOPIC_NOTES = 'notes'
TOPIC_CHAT = 'chat'
TOPIC_TRACKERS = 'trackers'

TOPICS = frozenset({
    TOPIC_ROOM, TOPIC_MEMBERS, TOPIC_CASES, TOPIC_TASKS, TOPIC_SCOPE, TOPIC_VULNERABILITIES, TOPIC_TIMELINES,
    TOPIC_DECISIONS, TOPIC_TEAMS, TOPIC_DATASTORE, TOPIC_SITREPS, TOPIC_NOTES, TOPIC_CHAT, TOPIC_TRACKERS,
})


def war_rooms_live_room(war_room_id) -> str:
    return f'war-room-{int(war_room_id)}'


def war_rooms_live_emit(war_room_id, topic):
    """Tell the open views of a war room that `topic` changed; never
    raises (the write already succeeded)."""
    from app.iris_engine.notifications.service import _safe_socket_emit

    try:
        if topic not in TOPICS:
            raise ValueError(f'Unknown war-room live topic {topic!r}')
        _safe_socket_emit(LIVE_EVENT, {'war_room_id': int(war_room_id), 'topic': topic},
                          room=war_rooms_live_room(war_room_id))
    except Exception:
        logger.exception('Could not push a war-room change')
