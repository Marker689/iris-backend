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

"""Presence in the AI workflow editor: who joins is checked as the REST
views check it, what the others see of them (identity, peer id, time a
node was focused) is set by the server, and leaving or disconnecting
tells the others."""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from flask import request

from app import app
from app.blueprints.socket_io_event_handlers import notification_event_handlers as handlers
from app.models.errors import ObjectNotFoundError

_HANDLERS = 'app.blueprints.socket_io_event_handlers.notification_event_handlers'
_OWNER = 13
_SID = 'sid-1'


def _session(user_id, workflow_id):
    if workflow_id != 7:
        raise ObjectNotFoundError()
    return {'room': 'ai-workflow-editors-7', 'workflow_id': 7, 'user_id': user_id, 'user_name': 'Owner'}


class TestsEditorPresence(TestCase):

    def setUp(self):
        self.emit = MagicMock()
        self.joined = MagicMock()
        self.left = MagicMock()
        self.user_id = _OWNER
        self.context = app.test_request_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        request.sid = _SID
        patchers = [
            patch(f'{_HANDLERS}._current_user_id', side_effect=lambda: self.user_id),
            patch(f'{_HANDLERS}.ai_workflows_editor_session', side_effect=_session),
            patch(f'{_HANDLERS}.join_room', self.joined),
            patch(f'{_HANDLERS}.leave_room', self.left),
            patch.object(handlers.socket_io, 'emit', self.emit),
            patch.dict(handlers._editor_sessions, clear=True),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _broadcast(self, index=-1):
        args, kwargs = self.emit.call_args_list[index]
        self.assertEqual('ai_workflow_editor', args[0])
        self.assertEqual('ai-workflow-editors-7', kwargs['room'])
        self.assertEqual(_SID, kwargs['skip_sid'])
        return args[1]

    def test_join_should_enter_the_room_and_say_hello(self):
        ack = handlers.on_ai_workflow_editor_join({'workflow_id': 7, 'node_id': 'n1'})
        self.assertTrue(ack['ok'])
        self.joined.assert_called_once_with('ai-workflow-editors-7')
        payload = self._broadcast()
        self.assertEqual('hello', payload['type'])
        self.assertEqual((ack['peer_id'], _OWNER, 'Owner', 'n1'),
                         (payload['peer_id'], payload['user_id'], payload['user_name'], payload['node_id']))
        self.assertEqual((ack['node_id'], ack['focused_at']), ('n1', payload['focused_at']))

    def test_identity_should_not_be_taken_from_the_client(self):
        handlers.on_ai_workflow_editor_join({'workflow_id': 7, 'user_id': 1, 'user_name': 'admin',
                                             'peer_id': 'forged', 'focused_at': 0})
        payload = self._broadcast()
        self.assertEqual((_OWNER, 'Owner'), (payload['user_id'], payload['user_name']))
        self.assertNotEqual('forged', payload['peer_id'])
        self.assertIsNone(payload['focused_at'])

    def test_workflow_the_user_cannot_see_should_be_refused(self):
        self.assertEqual({'ok': False}, handlers.on_ai_workflow_editor_join({'workflow_id': 8}))
        self.joined.assert_not_called()
        self.emit.assert_not_called()

    def test_malformed_payloads_should_be_refused(self):
        for data in (None, [], {'workflow_id': '7'}, {'workflow_id': True}, {'workflow_id': 7, 'node_id': 3},
                     {'workflow_id': 7, 'node_id': 'x' * 129}, {'workflow_id': 7, 'node_id': ''}):
            self.assertEqual({'ok': False}, handlers.on_ai_workflow_editor_join(data))
        self.emit.assert_not_called()

    def test_anonymous_socket_should_be_refused(self):
        self.user_id = None
        self.assertEqual({'ok': False}, handlers.on_ai_workflow_editor_join({'workflow_id': 7}))

    def test_update_should_need_a_join_first(self):
        self.assertEqual({'ok': False}, handlers.on_ai_workflow_editor_update({'workflow_id': 7, 'node_id': 'n1'}))
        self.emit.assert_not_called()

    def test_focus_time_should_move_only_when_the_node_changes(self):
        handlers.on_ai_workflow_editor_join({'workflow_id': 7})
        with patch(f'{_HANDLERS}.time.time', return_value=100.0):
            first = handlers.on_ai_workflow_editor_update({'workflow_id': 7, 'node_id': 'n1'})
        with patch(f'{_HANDLERS}.time.time', return_value=200.0):
            heartbeat = handlers.on_ai_workflow_editor_update({'workflow_id': 7, 'node_id': 'n1'})
            moved = handlers.on_ai_workflow_editor_update({'workflow_id': 7, 'node_id': 'n2'})
            cleared = handlers.on_ai_workflow_editor_update({'workflow_id': 7, 'node_id': None})
        self.assertEqual((100.0, 100.0, 200.0, None),
                         (first['focused_at'], heartbeat['focused_at'], moved['focused_at'], cleared['focused_at']))
        self.assertEqual('here', self._broadcast()['type'])

    def test_rejoin_should_keep_the_peer_id(self):
        first = handlers.on_ai_workflow_editor_join({'workflow_id': 7})
        again = handlers.on_ai_workflow_editor_join({'workflow_id': 7})
        self.assertEqual(first['peer_id'], again['peer_id'])

    def test_rooms_per_socket_should_be_capped(self):
        with patch(f'{_HANDLERS}.ai_workflows_editor_session',
                   side_effect=lambda user_id, wid: {'room': f'r{wid}', 'workflow_id': wid, 'user_id': user_id,
                                                     'user_name': 'Owner'}):
            acks = [handlers.on_ai_workflow_editor_join({'workflow_id': i}) for i in range(1, 11)]
        self.assertEqual([True] * 8 + [False] * 2, [a['ok'] for a in acks])

    def test_leave_should_tell_the_others(self):
        ack = handlers.on_ai_workflow_editor_join({'workflow_id': 7})
        handlers.on_ai_workflow_editor_leave({'workflow_id': 7})
        payload = self._broadcast()
        self.assertEqual(('gone', ack['peer_id']), (payload['type'], payload['peer_id']))
        self.left.assert_called_once_with('ai-workflow-editors-7')
        self.assertNotIn(_SID, handlers._editor_sessions)

    def test_disconnect_should_tell_the_others(self):
        ack = handlers.on_ai_workflow_editor_join({'workflow_id': 7})
        handlers.on_disconnect()
        payload = self._broadcast()
        self.assertEqual(('gone', ack['peer_id']), (payload['type'], payload['peer_id']))
        self.assertNotIn(_SID, handlers._editor_sessions)
