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

"""Live war-room views: joining the room of a war room is checked as the
REST views check a read, a push only carries the war room and the topic
that changed, and each successful write (REST or MCP) pushes the topic
it touched, to the war room itself or to the war rooms of its case."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

from flask import Response
from flask import request

from app import app
from app.blueprints.rest.v2.war_rooms import live as rest_live
from app.blueprints.socket_io_event_handlers import notification_event_handlers as handlers
from app.business import war_rooms_live as business_live
from app.iris_engine import war_rooms_live as engine_live
from app.models.authorization import Permissions
from app.models.authorization import WarRoomAccessLevel
from app.models.errors import ObjectNotFoundError

_HANDLERS = 'app.blueprints.socket_io_event_handlers.notification_event_handlers'
_BUSINESS = 'app.business.war_rooms_live'
_REST = 'app.blueprints.rest.v2.war_rooms.live'


class TestsWatchHandlers(TestCase):

    def setUp(self):
        self.joined = MagicMock()
        self.left = MagicMock()
        self.context = app.test_request_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        request.sid = 'sid-1'
        patchers = [
            patch(f'{_HANDLERS}._current_user_id', return_value=13),
            patch(f'{_HANDLERS}.join_room', self.joined),
            patch(f'{_HANDLERS}.leave_room', self.left),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_watch_should_join_the_room_the_access_check_returns(self):
        with patch(f'{_HANDLERS}.war_rooms_live_join_room', return_value='war-room-3') as join:
            ack = handlers.on_war_room_watch({'war_room_id': 3})
        self.assertEqual({'ok': True}, ack)
        join.assert_called_once_with(13, 3)
        self.joined.assert_called_once_with('war-room-3')

    def test_watch_should_refuse_a_war_room_the_user_cannot_read(self):
        with patch(f'{_HANDLERS}.war_rooms_live_join_room', side_effect=ObjectNotFoundError()):
            ack = handlers.on_war_room_watch({'war_room_id': 3})
        self.assertEqual({'ok': False}, ack)
        self.joined.assert_not_called()

    def test_watch_should_refuse_a_malformed_payload(self):
        self.assertEqual({'ok': False}, handlers.on_war_room_watch('war-room-3'))
        self.joined.assert_not_called()

    def test_unwatch_should_leave_without_an_access_check(self):
        handlers.on_war_room_unwatch({'war_room_id': 3})
        self.left.assert_called_once_with('war-room-3')

    def test_unwatch_should_ignore_anything_but_an_id(self):
        handlers.on_war_room_unwatch({'war_room_id': True})
        handlers.on_war_room_unwatch({'war_room_id': 'war-room-3'})
        self.left.assert_not_called()


class TestsJoinRoomAccess(TestCase):

    def _join(self, war_room_id=3, permissions=Permissions.war_rooms_read.value,
              access=WarRoomAccessLevel.read_only.value, active=True, exists=True):
        user = SimpleNamespace(id=13, active=active)
        with patch(f'{_BUSINESS}.get_user', return_value=user), \
                patch(f'{_BUSINESS}.war_room_exists', return_value=exists), \
                patch(f'{_BUSINESS}.ac_get_effective_permissions_of_user', return_value=permissions), \
                patch(f'{_BUSINESS}.ac_fast_check_user_has_war_room_access', return_value=access) as check:
            room = business_live.war_rooms_live_join_room(13, war_room_id)
        return room, check

    def test_a_reader_should_get_the_room(self):
        room, check = self._join()
        self.assertEqual('war-room-3', room)
        self.assertEqual(
            [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access], check.call_args.args[2])

    def test_a_user_without_access_should_be_refused(self):
        with self.assertRaises(ObjectNotFoundError):
            self._join(access=None)

    def test_a_user_without_the_war_room_permission_should_be_refused(self):
        with self.assertRaises(ObjectNotFoundError):
            self._join(permissions=Permissions.standard_user.value)

    def test_an_administrator_needs_no_war_room_permission(self):
        room, _ = self._join(permissions=Permissions.server_administrator.value)
        self.assertEqual('war-room-3', room)

    def test_a_deactivated_user_should_be_refused(self):
        with self.assertRaises(ObjectNotFoundError):
            self._join(active=False)

    def test_an_unknown_war_room_should_be_refused(self):
        with self.assertRaises(ObjectNotFoundError):
            self._join(exists=False)

    def test_only_a_positive_integer_id_is_accepted(self):
        for war_room_id in (True, '3', None, 0, -1):
            with self.assertRaises(ObjectNotFoundError):
                self._join(war_room_id=war_room_id)


class TestsEmit(TestCase):

    def test_a_push_should_carry_ids_and_the_topic_only(self):
        with patch('app.iris_engine.notifications.service._safe_socket_emit') as emit:
            engine_live.war_rooms_live_emit(3, engine_live.TOPIC_TASKS, actor_id=13)
        emit.assert_called_once_with('war_room_changed', {'war_room_id': 3, 'topic': 'tasks', 'actor_id': 13},
                                     room='war-room-3')

    def test_an_unknown_topic_should_not_be_pushed_nor_raise(self):
        with patch('app.iris_engine.notifications.service._safe_socket_emit') as emit:
            engine_live.war_rooms_live_emit(3, 'secrets')
        emit.assert_not_called()

    def test_a_case_change_should_reach_every_war_room_of_the_case(self):
        rows = [SimpleNamespace(war_room_id=4), SimpleNamespace(war_room_id=2)]
        with patch(f'{_BUSINESS}.war_rooms_for_case', return_value=rows), \
                patch(f'{_BUSINESS}.war_rooms_live_emit') as emit:
            business_live.war_rooms_live_notify_case(9, 'scope', actor_id=13)
        self.assertEqual([call(2, 'scope', actor_id=13), call(4, 'scope', actor_id=13)], emit.call_args_list)


class TestsAfterRequest(TestCase):

    def _run(self, path, method='POST', status=200, marks=()):
        emit = MagicMock()
        notify = MagicMock()
        with app.test_request_context(path, method=method), \
                patch(f'{_REST}._actor_id', return_value=13), \
                patch(f'{_REST}.war_rooms_live_emit', emit), \
                patch(f'{_REST}.war_rooms_live_notify_case', notify):
            for case_id, topic in marks:
                rest_live.war_rooms_live_mark_case(case_id, topic)
            response = Response(status=status)
            self.assertIs(response, rest_live.war_rooms_live_after_request(response))
        return emit, notify

    def test_a_war_room_write_should_push_its_section(self):
        cases = {
            ('/api/v2/war-rooms/3/tasks', 'POST'): 'tasks',
            ('/api/v2/war-rooms/3/tasks/5', 'PATCH'): 'tasks',
            ('/api/v2/war-rooms/3/scope/assets/push', 'POST'): 'scope',
            ('/api/v2/war-rooms/3/scope/staging/8', 'DELETE'): 'scope',
            ('/api/v2/war-rooms/3/timelines/events/2', 'PATCH'): 'timelines',
            ('/api/v2/war-rooms/3/decisions/2', 'PATCH'): 'decisions',
            ('/api/v2/war-rooms/3/cases', 'POST'): 'cases',
            ('/api/v2/war-rooms/3/members', 'POST'): 'members',
            ('/api/v2/war-rooms/3/notes-folders', 'POST'): 'notes',
            ('/api/v2/war-rooms/3', 'PATCH'): 'room',
            ('/api/v2/war-rooms/3/archive', 'POST'): 'room',
        }
        for (path, method), topic in cases.items():
            emit, notify = self._run(path, method)
            emit.assert_called_once_with(3, topic, actor_id=13)
            notify.assert_not_called()

    def test_a_case_write_should_push_the_war_rooms_of_the_case(self):
        cases = {
            ('/api/v2/cases/9/assets', 'POST'): 'scope',
            ('/api/v2/cases/9/iocs/4', 'PUT'): 'scope',
            ('/api/v2/cases/9/tasks/4', 'PUT'): 'tasks',
            ('/api/v2/cases/9/events/4', 'DELETE'): 'timelines',
            ('/api/v2/cases/9', 'PUT'): 'cases',
            ('/api/v2/cases/9/close', 'POST'): 'cases',
        }
        for (path, method), topic in cases.items():
            emit, notify = self._run(path, method)
            notify.assert_called_once_with(9, topic, actor_id=13)
            emit.assert_not_called()

    def test_a_route_naming_its_case_should_push_its_war_rooms(self):
        _, notify = self._run('/api/v2/iocs/4', 'PUT', marks=[(9, 'scope')])
        notify.assert_called_once_with(9, 'scope', actor_id=13)

    def test_reads_and_failures_should_push_nothing(self):
        for path, method, status in (('/api/v2/war-rooms/3/tasks', 'GET', 200),
                                     ('/api/v2/war-rooms/3/tasks', 'POST', 400),
                                     ('/api/v2/cases/9/assets', 'POST', 403),
                                     ('/api/v2/iocs/4', 'DELETE', 404)):
            emit, notify = self._run(path, method, status, marks=[(9, 'scope')])
            emit.assert_not_called()
            notify.assert_not_called()

    def test_writes_with_nothing_to_show_should_push_nothing(self):
        for path in ('/api/v2/cases/9/notes', '/api/v2/war-rooms/categories', '/api/v2/alerts'):
            emit, notify = self._run(path)
            emit.assert_not_called()
            notify.assert_not_called()


class TestsAfterMcpTool(TestCase):

    def _run(self, tool_name, args, war_room_scoped=False, case_scoped=False):
        emit = MagicMock()
        notify = MagicMock()
        with patch(f'{_REST}._actor_id', return_value=13), \
                patch(f'{_REST}.war_rooms_live_emit', emit), \
                patch(f'{_REST}.war_rooms_live_notify_case', notify):
            rest_live.war_rooms_live_after_mcp_tool(tool_name, args, war_room_scoped, case_scoped)
        return emit, notify

    def test_a_war_room_tool_should_push_its_section(self):
        cases = {
            'iris_war_rooms_update': 'room',
            'iris_war_room_tasks_update': 'tasks',
            'iris_war_room_scope_staging_push': 'scope',
            'iris_war_room_timeline_events_create': 'timelines',
            'iris_war_room_decisions_create': 'decisions',
            'iris_war_room_cases_attach': 'cases',
            'iris_war_room_chat_post': 'chat',
        }
        for tool_name, topic in cases.items():
            emit, _ = self._run(tool_name, {'war_room_id': 3}, war_room_scoped=True)
            emit.assert_called_once_with(3, topic, actor_id=13)

    def test_a_case_tool_should_push_the_war_rooms_of_the_case(self):
        cases = {
            'iris_case_assets_create': 'scope',
            'iris_case_iocs_delete': 'scope',
            'iris_case_tasks_set_status': 'tasks',
            'iris_case_events_update': 'timelines',
            'iris_cases_close': 'cases',
        }
        for tool_name, topic in cases.items():
            _, notify = self._run(tool_name, {'case_identifier': 9}, case_scoped=True)
            notify.assert_called_once_with(9, topic, actor_id=13)

    def test_a_tool_with_nothing_to_show_should_push_nothing(self):
        emit, notify = self._run('iris_case_notes_create', {'case_identifier': 9}, case_scoped=True)
        notify.assert_not_called()
        emit.assert_not_called()
