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

"""Unit tests for Yuki's war-room edit tools: room update, sitrep, task
and decision edits. The summary and the sitreps are collaborative
documents, so an edit has to reach the Y.Doc and a read has to come from
it. The business layer and the collab helpers are mocked."""

from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.tools import war_rooms as war_rooms_tools


_WR = 'app.blueprints.rest.v2.mcp.tools.war_rooms'


def _user():
    user = MagicMock()
    user._get_current_object.return_value = SimpleNamespace(id=5)
    return user


def _room(**kwargs):
    fields = {'war_room_id': 3, 'name': 'Ransomware', 'description': 'column summary', 'state': 'open',
              'severity_id': None, 'created_at': None, 'archived_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _sitrep(**kwargs):
    fields = {'sitrep_id': 7, 'version': 1, 'title': 'SitRep 1', 'body_md': 'column body',
              'authored_by_id': 5, 'authored_at': None, 'published': False}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class TestWarRoomUpdate(TestCase):

    def _run(self, args, previous_state='open', updated=None):
        update = MagicMock(return_value=updated or _room())
        self.replace = MagicMock()
        self.emit = MagicMock()
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_WR}.war_rooms_biz.war_room_get', return_value=_room(state=previous_state)), \
                patch(f'{_WR}.war_rooms_biz.war_room_update', update), \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event', self.emit), \
                patch(f'{_WR}.collab_replace_markdown', self.replace):
            war_rooms_tools.iris_war_rooms_update({'war_room_id': 3, **args})
        return update

    def test_new_summary_is_pushed_into_the_ydoc(self):
        self._run({'description': '# Scope'})
        self.replace.assert_called_once_with('war-room-summary:3', '# Scope')

    def test_rename_leaves_the_ydoc_alone(self):
        update = self._run({'name': 'Renamed'})
        self.assertEqual('Renamed', update.call_args.kwargs['name'])
        self.replace.assert_not_called()

    def test_state_change_is_announced_in_the_stream(self):
        self._run({'state': 'active'}, updated=_room(state='active'))
        self.assertEqual('War room state changed to active', self.emit.call_args.args[2])

    def test_unchanged_state_is_not_announced(self):
        self._run({'state': 'open'})
        self.emit.assert_not_called()

    def test_empty_update_is_refused(self):
        with self.assertRaises(MCPError):
            self._run({})


class TestWarRoomGet(TestCase):

    def test_summary_is_read_from_the_ydoc(self):
        with patch(f'{_WR}.war_rooms_biz.war_room_get', return_value=_room()), \
                patch(f'{_WR}.collab_current_markdown', return_value='live summary') as current:
            data = war_rooms_tools.iris_war_rooms_get({'war_room_id': 3})
        current.assert_called_once_with('war-room-summary:3', 'column summary')
        self.assertEqual('live summary', data['description'])


class TestWarRoomSitreps(TestCase):

    def test_list_reads_the_bodies_from_the_ydoc(self):
        with patch(f'{_WR}.war_room_sitreps_biz.sitrep_list', return_value=[_sitrep()]), \
                patch(f'{_WR}.collab_current_markdown', return_value='live body') as current:
            data = war_rooms_tools.iris_war_room_sitreps_list({'war_room_id': 3})
        current.assert_called_once_with('sitrep:7', 'column body')
        self.assertEqual('live body', data['sitreps'][0]['body_md'])

    def _update(self, args):
        update = MagicMock(return_value=_sitrep())
        replace = MagicMock()
        with patch(f'{_WR}.war_room_sitreps_biz.sitrep_update', update), \
                patch(f'{_WR}.collab_replace_markdown', replace):
            war_rooms_tools.iris_war_room_sitreps_update({'war_room_id': 3, 'sitrep_id': 7, **args})
        return update, replace

    def test_body_edit_is_pushed_into_the_ydoc(self):
        update, replace = self._update({'body_md': '## Status'})
        update.assert_called_once_with(3, 7, title=None, body_md='## Status')
        replace.assert_called_once_with('sitrep:7', '## Status')

    def test_title_only_edit_leaves_the_ydoc_alone(self):
        _, replace = self._update({'title': 'SitRep 1 (rev)'})
        replace.assert_not_called()


class TestWarRoomTasksUpdate(TestCase):

    def _run(self, args, closed_at=None):
        task = SimpleNamespace(task_id=11, title='Reset passwords', description=None, status_id=1,
                               assignee_id=None, created_by_id=5, created_at=None, due_at=None,
                               closed_at=closed_at)
        self.biz = MagicMock()
        self.biz.war_room_task_get.return_value = task
        self.biz.war_room_task_update.return_value = task
        self.biz.war_room_task_close.return_value = task
        self.biz.war_room_task_reopen.return_value = task
        self.biz.war_room_task_teams.return_value = {}
        self.emit = MagicMock()
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_WR}.war_room_tasks_biz', self.biz), \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event', self.emit):
            war_rooms_tools.iris_war_room_tasks_update({'war_room_id': 3, 'task_id': 11, **args})

    def test_fields_go_through_the_business_layer(self):
        self._run({'status_id': 2, 'due_at': '2026-10-20'})
        kwargs = self.biz.war_room_task_update.call_args.kwargs
        self.assertEqual(2, kwargs['status_id'])
        self.assertEqual(2026, kwargs['due_at'].year)
        self.assertEqual(5, kwargs['updated_by_id'])

    def test_closing_an_open_task_closes_it_and_announces_it(self):
        self._run({'closed': True})
        self.biz.war_room_task_close.assert_called_once_with(3, 11, closed_by_id=5)
        self.biz.war_room_task_update.assert_not_called()
        self.assertEqual('task_completed', self.emit.call_args.args[1])

    def test_closing_a_closed_task_does_nothing(self):
        self._run({'closed': True}, closed_at=datetime(2026, 10, 1))
        self.biz.war_room_task_close.assert_not_called()
        self.emit.assert_not_called()

    def test_reopening_a_closed_task(self):
        self._run({'closed': False}, closed_at=datetime(2026, 10, 1))
        self.biz.war_room_task_reopen.assert_called_once_with(3, 11)

    def test_bad_due_date_is_refused(self):
        with self.assertRaises(MCPError):
            self._run({'due_at': 'tomorrow'})


class TestWarRoomDecisionsUpdate(TestCase):

    def _run(self, args, readable=(), already_linked=()):
        decision = SimpleNamespace(decision_id=4, number=2, title='Isolate DC', status='proposed')
        self.biz = MagicMock()
        self.biz.war_room_decisions_get.return_value = decision
        self.biz.war_room_decisions_linked_case_ids.return_value = set(already_linked)
        self.biz.war_room_decisions_parse_case_ids.side_effect = list
        self.biz.war_room_decisions_update.return_value = SimpleNamespace(
            decision_id=4, number=2, title='Isolate DC', status=args.get('status', 'proposed'))
        self.biz.war_room_decisions_referenced_case_ids.return_value = set()
        self.biz.war_room_decisions_serialize.return_value = [{'decision_id': 4}]
        self.emit = MagicMock()
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_WR}.war_room_decisions_biz', self.biz), \
                patch(f'{_WR}.ac_fast_check_current_user_has_cases_access', return_value=list(readable)), \
                patch(f'{_WR}.war_room_chat_biz.emit_system_event', self.emit):
            war_rooms_tools.iris_war_room_decisions_update({'war_room_id': 3, 'decision_id': 4, **args})

    def test_linking_an_unreadable_case_is_refused(self):
        with self.assertRaises(MCPError):
            self._run({'case_ids': [8]}, readable=())
        self.biz.war_room_decisions_update.assert_not_called()

    def test_keeping_an_already_linked_case_needs_no_access(self):
        self._run({'case_ids': [8]}, readable=(), already_linked=(8,))
        self.biz.war_room_decisions_update.assert_called_once()

    def test_approval_is_announced(self):
        self._run({'status': 'approved'})
        self.assertEqual('D-2 approved: Isolate DC', self.emit.call_args.args[2])

    def test_asset_links_are_not_forwarded(self):
        self._run({'title': 'Isolate DCs', 'asset_ids': [1]})
        self.assertNotIn('asset_ids', self.biz.war_room_decisions_update.call_args.args[2])
