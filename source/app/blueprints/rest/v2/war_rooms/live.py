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

"""Which successful write touches which live view of which war room.

One `after_request` hook on the v2 API reads it off the route: a write
under `/war-rooms/<id>/<section>` changes that section of the room, one
under `/cases/<id>/<section>` the same section of every war room the
case is attached to. The few routes that address a case object by its
own id (`/iocs/<id>`, `/tasks/<id>`, …) name the case themselves with
`war_rooms_live_mark_case`. The MCP tools go through
`war_rooms_live_after_mcp_tool`, the same mapping keyed by tool name."""

import logging

from flask import request

from app.business.war_rooms_live import war_rooms_live_notify_case
from app.iris_engine.war_rooms_live import TOPIC_CASES
from app.iris_engine.war_rooms_live import TOPIC_CHAT
from app.iris_engine.war_rooms_live import TOPIC_DATASTORE
from app.iris_engine.war_rooms_live import TOPIC_DECISIONS
from app.iris_engine.war_rooms_live import TOPIC_MEMBERS
from app.iris_engine.war_rooms_live import TOPIC_NOTES
from app.iris_engine.war_rooms_live import TOPIC_ROOM
from app.iris_engine.war_rooms_live import TOPIC_SCOPE
from app.iris_engine.war_rooms_live import TOPIC_SITREPS
from app.iris_engine.war_rooms_live import TOPIC_TASKS
from app.iris_engine.war_rooms_live import TOPIC_TEAMS
from app.iris_engine.war_rooms_live import TOPIC_TIMELINES
from app.iris_engine.war_rooms_live import TOPIC_TRACKERS
from app.iris_engine.war_rooms_live import TOPIC_VULNERABILITIES
from app.iris_engine.war_rooms_live import war_rooms_live_emit


logger = logging.getLogger(__name__)

_WRITE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# Request-scoped (unlike `g`, which an app context outliving a request keeps)
_MARKS_KEY = 'iris.war_rooms_live.case_marks'

# First path segment after `/war-rooms/<id>` -> topic
_WAR_ROOM_SECTIONS = {
    '': TOPIC_ROOM,
    'category': TOPIC_ROOM,
    'archive': TOPIC_ROOM,
    'board': TOPIC_ROOM,
    'members': TOPIC_MEMBERS,
    'cases': TOPIC_CASES,
    'tasks': TOPIC_TASKS,
    'scope': TOPIC_SCOPE,
    'vulnerabilities': TOPIC_VULNERABILITIES,
    'timelines': TOPIC_TIMELINES,
    'decisions': TOPIC_DECISIONS,
    'teams': TOPIC_TEAMS,
    'datastore': TOPIC_DATASTORE,
    'sitreps': TOPIC_SITREPS,
    'notes': TOPIC_NOTES,
    'notes-folders': TOPIC_NOTES,
    'note-shares': TOPIC_NOTES,
    'chat': TOPIC_CHAT,
    'trackers': TOPIC_TRACKERS,
    'tracker-steps': TOPIC_TRACKERS,
}

# First path segment after `/cases/<id>` -> topic of the attached rooms
_CASE_SECTIONS = {
    '': TOPIC_CASES,
    'close': TOPIC_CASES,
    'reopen': TOPIC_CASES,
    'assets': TOPIC_SCOPE,
    'iocs': TOPIC_SCOPE,
    'tasks': TOPIC_TASKS,
    'events': TOPIC_TIMELINES,
    'timelines': TOPIC_TIMELINES,
    'vulnerabilities': TOPIC_VULNERABILITIES,
}

_WAR_ROOM_PLACEHOLDER = '/war-rooms/<int:war_room_id>'
_CASE_PLACEHOLDERS = ('/cases/<int:case_identifier>', '/cases/<int:identifier>')

# MCP tool name, past its `iris_war_room(s)_` / `iris_case(s)_` prefix,
# first word -> topic
_WAR_ROOM_TOOL_SECTIONS = {
    'update': TOPIC_ROOM,
    'members': TOPIC_MEMBERS,
    'cases': TOPIC_CASES,
    'tasks': TOPIC_TASKS,
    'scope': TOPIC_SCOPE,
    'vulnerabilities': TOPIC_VULNERABILITIES,
    'timeline': TOPIC_TIMELINES,
    'timelines': TOPIC_TIMELINES,
    'decisions': TOPIC_DECISIONS,
    'teams': TOPIC_TEAMS,
    'datastore': TOPIC_DATASTORE,
    'sitreps': TOPIC_SITREPS,
    'notes': TOPIC_NOTES,
    'chat': TOPIC_CHAT,
}
_CASE_TOOL_SECTIONS = {
    'update': TOPIC_CASES,
    'close': TOPIC_CASES,
    'reopen': TOPIC_CASES,
    'assets': TOPIC_SCOPE,
    'iocs': TOPIC_SCOPE,
    'tasks': TOPIC_TASKS,
    'events': TOPIC_TIMELINES,
    'vulnerabilities': TOPIC_VULNERABILITIES,
}


def _section(rule, placeholder):
    """First path segment after `placeholder` in `rule`, '' when the
    rule ends there, None when it is not under it."""
    _, found, tail = rule.partition(placeholder)
    if not found or (tail and not tail.startswith('/')):
        return None
    return tail.strip('/').split('/', 1)[0]


def war_rooms_live_mark_case(case_id, topic):
    """Have the war rooms of `case_id` told `topic` changed once the
    current request succeeded (routes that don't carry the case id)."""
    marks = request.environ.setdefault(_MARKS_KEY, set())
    marks.add((int(case_id), topic))


def _changes(rule, view_args):
    """`([(war_room_id, topic)], [(case_id, topic)])` a write to `rule`
    stands for."""
    rooms = []
    cases = set(request.environ.get(_MARKS_KEY) or ())
    section = _section(rule, _WAR_ROOM_PLACEHOLDER)
    if section is not None and section in _WAR_ROOM_SECTIONS and view_args.get('war_room_id'):
        rooms.append((view_args['war_room_id'], _WAR_ROOM_SECTIONS[section]))
    for placeholder in _CASE_PLACEHOLDERS:
        section = _section(rule, placeholder)
        if section is None or section not in _CASE_SECTIONS:
            continue
        case_id = view_args.get('case_identifier') or view_args.get('identifier')
        if case_id:
            cases.add((case_id, _CASE_SECTIONS[section]))
        break
    return rooms, cases


def war_rooms_live_after_request(response):
    """Push the war-room views a successful v2 write changed. Never
    alters nor fails the response."""
    try:
        if request.method in _WRITE_METHODS and response.status_code < 400 and request.url_rule is not None:
            rooms, cases = _changes(request.url_rule.rule, request.view_args or {})
            for war_room_id, topic in rooms:
                war_rooms_live_emit(war_room_id, topic)
            for case_id, topic in sorted(cases):
                war_rooms_live_notify_case(case_id, topic)
    except Exception:
        logger.exception('Could not push the war-room changes of %s', request.path)
    return response


def _tool_topic(tool_name, prefixes, sections):
    for prefix in prefixes:
        if tool_name.startswith(prefix):
            return sections.get(tool_name[len(prefix):].split('_', 1)[0])
    return None


def war_rooms_live_after_mcp_tool(tool_name, args, war_room_scoped, case_scoped):
    """Push the war-room views a successful mutating MCP tool changed;
    never raises."""
    try:
        if war_room_scoped and args.get('war_room_id'):
            topic = _tool_topic(tool_name, ('iris_war_rooms_', 'iris_war_room_'), _WAR_ROOM_TOOL_SECTIONS)
            if topic is not None:
                war_rooms_live_emit(args['war_room_id'], topic)
        if case_scoped and args.get('case_identifier'):
            topic = _tool_topic(tool_name, ('iris_cases_', 'iris_case_'), _CASE_TOOL_SECTIONS)
            if topic is not None:
                war_rooms_live_notify_case(args['case_identifier'], topic)
    except Exception:
        logger.exception('Could not push the war-room changes of MCP tool %s', tool_name)
