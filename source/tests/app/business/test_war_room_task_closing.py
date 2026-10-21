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

"""Closing a war-room task and moving it to a closed status (the board's
Done column) are the same thing, whichever way it is done."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.war_room_tasks import war_room_task_close
from app.business.war_room_tasks import war_room_task_reopen
from app.business.war_room_tasks import war_room_task_update


_TASKS = 'app.business.war_room_tasks'
_STATUSES = {'To do': 1, 'In progress': 2, 'Done': 4, 'Canceled': 5}
_CLOSED_AT = datetime.datetime(2026, 10, 1)


def _task(status_id=None, closed_at=None, closed_by_id=None):
    return SimpleNamespace(
        task_id=4, war_room_id=10, title='Contain', description=None, status_id=status_id,
        assignee_id=None, due_at=None, tags=None, parent_task_id=None, source_case_id=None,
        source_case_task_id=None, closed_at=closed_at, closed_by_id=closed_by_id,
    )


class _Base(TestCase):

    def setUp(self):
        patches = [
            patch(f'{_TASKS}._task_status_ids_by_name',
                  side_effect=lambda names: {n: _STATUSES[n] for n in names if n in _STATUSES}),
            patch(f'{_TASKS}.db'),
            patch(f'{_TASKS}.track_activity'),
            patch(f'{_TASKS}.call_modules_hook', side_effect=lambda _name, data, **_kw: data),
            patch(f'{_TASKS}._notify_assigned_teams'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, task, function, *args, **kwargs):
        with patch(f'{_TASKS}.war_room_task_get', return_value=task):
            return function(10, 4, *args, **kwargs)


class TestCloseAndReopen(_Base):

    def test_closing_moves_the_task_to_done(self):
        task = self._run(_task(status_id=2), war_room_task_close, closed_by_id=7)
        self.assertIsNotNone(task.closed_at)
        self.assertEqual(7, task.closed_by_id)
        self.assertEqual(_STATUSES['Done'], task.status_id)

    def test_closing_keeps_a_canceled_task_canceled(self):
        task = self._run(_task(status_id=_STATUSES['Canceled']), war_room_task_close, closed_by_id=7)
        self.assertEqual(_STATUSES['Canceled'], task.status_id)

    def test_closing_keeps_the_status_when_there_is_no_done_status(self):
        with patch(f'{_TASKS}._task_status_ids_by_name', return_value={}):
            task = self._run(_task(status_id=2), war_room_task_close, closed_by_id=7)
        self.assertEqual(2, task.status_id)
        self.assertIsNotNone(task.closed_at)

    def test_reopening_a_done_task_moves_it_back_to_to_do(self):
        task = self._run(_task(status_id=_STATUSES['Done'], closed_at=_CLOSED_AT, closed_by_id=7),
                         war_room_task_reopen)
        self.assertIsNone(task.closed_at)
        self.assertIsNone(task.closed_by_id)
        self.assertEqual(_STATUSES['To do'], task.status_id)

    def test_reopening_keeps_an_open_status(self):
        task = self._run(_task(status_id=2, closed_at=_CLOSED_AT), war_room_task_reopen)
        self.assertEqual(2, task.status_id)


class TestStatusChange(_Base):

    def test_moving_to_done_closes_the_task(self):
        task = self._run(_task(status_id=2), war_room_task_update,
                         status_id=_STATUSES['Done'], updated_by_id=7)
        self.assertIsNotNone(task.closed_at)
        self.assertEqual(7, task.closed_by_id)

    def test_moving_to_canceled_closes_the_task(self):
        task = self._run(_task(status_id=2), war_room_task_update,
                         status_id=_STATUSES['Canceled'], updated_by_id=7)
        self.assertIsNotNone(task.closed_at)

    def test_moving_out_of_done_reopens_the_task(self):
        task = self._run(_task(status_id=_STATUSES['Done'], closed_at=_CLOSED_AT, closed_by_id=7),
                         war_room_task_update, status_id=2, updated_by_id=7)
        self.assertIsNone(task.closed_at)
        self.assertIsNone(task.closed_by_id)

    def test_moving_to_no_status_reopens_the_task(self):
        task = self._run(_task(status_id=_STATUSES['Done'], closed_at=_CLOSED_AT),
                         war_room_task_update, status_id=None, updated_by_id=7)
        self.assertIsNone(task.closed_at)

    def test_moving_between_closed_statuses_keeps_the_closing(self):
        task = self._run(_task(status_id=_STATUSES['Done'], closed_at=_CLOSED_AT, closed_by_id=3),
                         war_room_task_update, status_id=_STATUSES['Canceled'], updated_by_id=7)
        self.assertEqual(_CLOSED_AT, task.closed_at)
        self.assertEqual(3, task.closed_by_id)

    def test_an_update_without_status_change_leaves_a_closed_task_closed(self):
        # A task closed before statuses were synced may sit in an open status.
        task = self._run(_task(status_id=2, closed_at=_CLOSED_AT), war_room_task_update,
                         title='Contain the host', updated_by_id=7)
        self.assertEqual(_CLOSED_AT, task.closed_at)
