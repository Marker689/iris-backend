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

"""Unit tests for the war-room board widgets (no database)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_board_widgets as widgets
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_BIZ = 'app.business.war_room_board_widgets'
_COLUMNS = [{'key': 'c_sector', 'label': 'Sector', 'type': 'select'},
            {'key': 'c_note', 'label': 'Note', 'type': 'text'}]


def _ctx():
    return widgets.WarRoomBoardContext({4: _COLUMNS, 5: []}, [1, 2, 3], [8])


def _validate(*items):
    return widgets.war_room_board_widgets_validate(list(items), _ctx())


class TestDefault(TestCase):

    def test_the_default_board_is_valid_and_has_every_builtin_once(self):
        default = widgets.war_room_board_widgets_default()
        self.assertEqual(widgets.war_room_board_widgets_validate(default, _ctx()), default)
        types = [w['type'] for w in default]
        self.assertEqual(set(widgets.BUILTIN_TYPES), set(types) - {'tasks'})
        self.assertEqual(len(types), len(set(types)))

    def test_panels_have_their_default_column(self):
        by_id = {w['id']: w for w in widgets.war_room_board_widgets_default()}
        self.assertEqual(by_id['cases']['column'], 'main')
        self.assertEqual(by_id['tasks']['column'], 'side')
        self.assertIsNone(by_id['kpi_tasks']['column'])


class TestValidation(TestCase):

    def test_builtins_are_identified_by_their_type(self):
        widget, = _validate({'type': 'decisions', 'column': 'main', 'title': '  Choices '})
        self.assertEqual(widget, {'id': 'decisions', 'type': 'decisions', 'title': 'Choices',
                                  'column': 'main', 'config': {}})
        with self.assertRaises(BusinessProcessingError):
            _validate({'id': 'other', 'type': 'decisions'})

    def test_configurable_widgets_get_an_id(self):
        first, second = _validate({'type': 'note'}, {'type': 'note', 'id': 'mine'})
        self.assertRegex(first['id'], r'^w_[0-9a-f]{8}$')
        self.assertEqual(second['id'], 'mine')
        self.assertEqual(first['config'], {'text': ''})

    def test_tiles_have_no_column(self):
        widget, = _validate({'type': 'tracker_count', 'column': 'side', 'config': {'tracker_id': 4}})
        self.assertIsNone(widget['column'])
        self.assertEqual(widget['config'], {'tracker_id': 4, 'step_ids': [], 'of_total': True, 'filter': None})

    def test_tracker_widgets_are_normalised(self):
        progress, listed, breakdown = _validate(
            {'type': 'tracker_progress', 'config': {'tracker_id': 4, 'step_ids': [2, 1, 2], 'display': 'donut'}},
            {'type': 'tracker_list', 'column': 'side', 'config': {
                'tracker_id': 4, 'filter': {'column_key': 'c_sector', 'values': ['Bank', 'Bank', 3]}}},
            {'type': 'tracker_breakdown', 'config': {'tracker_id': 4, 'column_key': 'c_sector'}})
        self.assertEqual(progress['config'], {'tracker_id': 4, 'step_ids': [2, 1], 'display': 'donut',
                                              'filter': None})
        self.assertEqual(listed['column'], 'side')
        self.assertEqual(listed['config']['filter'], {'column_key': 'c_sector', 'values': ['Bank', 3]})
        self.assertEqual(breakdown['config'], {'tracker_id': 4, 'column_key': 'c_sector', 'step_ids': None})

    def test_tasks_and_cases(self):
        tasks, cases = _validate({'type': 'tasks', 'id': 'tasks', 'config': {'in_progress_only': True,
                                                                            'team_ids': [8]}},
                                 {'type': 'cases', 'config': {'highlight_step_ids': [3], 'tracker_ids': [5]}})
        self.assertEqual(tasks['config'], {'in_progress_only': True, 'team_ids': [8]})
        self.assertEqual(cases['config'], {'highlight_step_ids': [3], 'tracker_ids': [5]})

    def test_invalid_widgets_are_refused(self):
        for item in ('note',
                     {'type': 'nope'},
                     {'type': 'note', 'id': 'Has Spaces'},
                     {'type': 'note', 'id': 'flags'},
                     {'type': 'note', 'column': 'left'},
                     {'type': 'note', 'config': []},
                     {'type': 'note', 'title': 'x' * 129},
                     {'type': 'tracker_count', 'config': {}},
                     {'type': 'tracker_count', 'config': {'tracker_id': 99}},
                     {'type': 'tracker_count', 'config': {'tracker_id': 4, 'step_ids': [7]}},
                     {'type': 'tracker_count', 'config': {'tracker_id': 4, 'of_total': 'yes'}},
                     {'type': 'tracker_progress', 'config': {'tracker_id': 4, 'display': 'pie'}},
                     {'type': 'tracker_list', 'config': {'tracker_id': 4, 'filter': {'column_key': 'c_x',
                                                                                     'values': ['a']}}},
                     {'type': 'tracker_list', 'config': {'tracker_id': 4, 'filter': {'column_key': 'c_sector',
                                                                                     'values': []}}},
                     {'type': 'tracker_breakdown', 'config': {'tracker_id': 4, 'column_key': 'c_note'}},
                     {'type': 'tasks', 'config': {'team_ids': [9]}},
                     {'type': 'cases', 'config': {'tracker_ids': [99]}}):
            with self.subTest(item=item), self.assertRaises(BusinessProcessingError):
                _validate(item)

    def test_ids_are_unique_and_boards_are_bounded(self):
        with self.assertRaises(BusinessProcessingError):
            _validate({'type': 'flags'}, {'type': 'flags'})
        with self.assertRaises(BusinessProcessingError):
            _validate({'type': 'note', 'id': 'a'}, {'type': 'note', 'id': 'a'})
        with self.assertRaises(BusinessProcessingError):
            _validate(*[{'type': 'note'}] * (widgets.WAR_ROOM_BOARD_MAX_WIDGETS + 1))
        with self.assertRaises(BusinessProcessingError):
            widgets.war_room_board_widgets_validate({'widgets': []}, _ctx())


class _Db(TestCase):

    def setUp(self):
        self.board = None
        self.set_board = MagicMock(side_effect=self._set)
        self.track = MagicMock()
        for name, value in (('war_room_board_db_get_board', lambda _war_room_id: self.board),
                            ('war_room_board_db_set_board', self.set_board),
                            ('track_activity', self.track)):
            patcher = patch(f'{_BIZ}.{name}', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(widgets.WarRoomBoardContext, 'of_room', return_value=_ctx())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set(self, _war_room_id, board, commit=True):
        self.board = board
        return True


class TestReadWrite(_Db):

    def test_a_room_without_board_gets_the_default_one(self):
        self.assertEqual(widgets.war_room_board_widgets_get(7),
                         (widgets.war_room_board_widgets_default(), False))
        self.board = {'widgets': 'broken'}
        self.assertIsNone(widgets.war_room_board_widgets_stored(7))

    def test_set_and_reset(self):
        result = widgets.war_room_board_widgets_set(7, {'widgets': [{'type': 'flags'}, {'type': 'note', 'id': 'n'}]})
        self.assertEqual([w['id'] for w in result[0]], ['flags', 'n'])
        self.assertTrue(result[1])
        self.assertEqual(self.board['widgets'], result[0])
        self.assertEqual(self.track.call_args.args[0], 'updated the war-room board')
        result = widgets.war_room_board_widgets_set(7, {'widgets': None})
        self.assertFalse(result[1])
        self.assertIsNone(self.board)
        self.assertEqual(self.track.call_args.args[0], 'reset the war-room board')

    def test_set_refuses_bad_bodies_and_unknown_rooms(self):
        for body in ([], {}, {'widgets': 'x'}):
            with self.subTest(body=body), self.assertRaises(BusinessProcessingError):
                widgets.war_room_board_widgets_set(7, body)
        self.set_board.side_effect = None
        self.set_board.return_value = False
        with self.assertRaises(ObjectNotFoundError):
            widgets.war_room_board_widgets_set(7, {'widgets': None})
        self.track.assert_not_called()

    def test_append_starts_from_the_default_board_without_committing(self):
        added = widgets.war_room_board_widgets_append(7, [{'type': 'tracker_count', 'config': {'tracker_id': 4}}])
        self.assertEqual(added, 1)
        self.assertEqual(self.set_board.call_args.kwargs, {'commit': False})
        default = widgets.war_room_board_widgets_default()
        self.assertEqual(self.board['widgets'][:len(default)], default)
        self.assertEqual(self.board['widgets'][-1]['type'], 'tracker_count')

    def test_append_drops_what_does_not_fit(self):
        self.board = {'widgets': [{'id': f'n{i}', 'type': 'note', 'title': None, 'column': 'side',
                                   'config': {'text': ''}} for i in range(widgets.WAR_ROOM_BOARD_MAX_WIDGETS - 1)]}
        added = widgets.war_room_board_widgets_append(7, [{'type': 'note'}, {'type': 'note'}])
        self.assertEqual(added, 1)
        self.assertEqual(len(self.board['widgets']), widgets.WAR_ROOM_BOARD_MAX_WIDGETS)
        self.assertEqual(widgets.war_room_board_widgets_append(7, []), 0)


class TestPrune(_Db):

    def setUp(self):
        super().setUp()
        self.board = {'widgets': [
            {'id': 'cases', 'type': 'cases', 'title': None, 'column': 'main',
             'config': {'highlight_step_ids': [1, 2], 'tracker_ids': [4, 5]}},
            {'id': 'w_count', 'type': 'tracker_count', 'title': None, 'column': None,
             'config': {'tracker_id': 4, 'step_ids': [2], 'of_total': True,
                        'filter': {'column_key': 'c_sector', 'values': ['Bank']}}},
            {'id': 'w_breakdown', 'type': 'tracker_breakdown', 'title': None, 'column': 'main',
             'config': {'tracker_id': 4, 'column_key': 'c_sector', 'step_ids': None}},
            {'id': 'w_other', 'type': 'tracker_list', 'title': None, 'column': 'main',
             'config': {'tracker_id': 5, 'step_ids': [], 'filter': None}},
            {'id': 'tasks', 'type': 'tasks', 'title': None, 'column': 'side',
             'config': {'in_progress_only': False, 'team_ids': [8, 9]}},
        ]}

    def _config(self, widget_id):
        return next(w['config'] for w in self.board['widgets'] if w['id'] == widget_id)

    def _ids(self):
        return [w['id'] for w in self.board['widgets']]

    def test_a_deleted_tracker_takes_its_widgets(self):
        widgets.war_room_board_widgets_prune(7, tracker_id=4)
        self.assertEqual(self._ids(), ['cases', 'w_other', 'tasks'])
        self.assertEqual(self._config('cases')['tracker_ids'], [5])
        self.assertEqual(self.set_board.call_args.kwargs, {'commit': False})

    def test_a_deleted_step_is_forgotten(self):
        widgets.war_room_board_widgets_prune(7, step_id=2)
        self.assertEqual(self._config('cases')['highlight_step_ids'], [1])
        self.assertEqual(self._config('w_count')['step_ids'], [])
        self.assertIsNone(self._config('w_breakdown')['step_ids'])

    def test_a_dropped_column_takes_its_breakdown_and_filters(self):
        widgets.war_room_board_widgets_prune(7, tracker_columns=(4, [{'key': 'c_note'}]))
        self.assertEqual(self._ids(), ['cases', 'w_count', 'w_other', 'tasks'])
        self.assertIsNone(self._config('w_count')['filter'])
        self.set_board.reset_mock()
        widgets.war_room_board_widgets_prune(7, tracker_columns=(5, []))
        self.set_board.assert_not_called()

    def test_a_deleted_team_is_forgotten(self):
        widgets.war_room_board_widgets_prune(7, team_id=9)
        self.assertEqual(self._config('tasks')['team_ids'], [8])

    def test_nothing_to_prune_on_the_default_board(self):
        self.board = None
        widgets.war_room_board_widgets_prune(7, tracker_id=4)
        self.set_board.assert_not_called()


class TestContext(TestCase):

    def test_of_room(self):
        with patch(f'{_BIZ}.war_room_trackers_db_trackers',
                   return_value=[SimpleNamespace(id=4, columns=_COLUMNS), SimpleNamespace(id=5, columns=None)]), \
                patch(f'{_BIZ}.war_room_trackers_db_steps', return_value=[SimpleNamespace(id=1)]), \
                patch(f'{_BIZ}.war_room_board_db_team_ids', return_value={8}):
            ctx = widgets.WarRoomBoardContext.of_room(7)
        self.assertEqual(ctx.tracker_columns, {4: _COLUMNS, 5: []})
        self.assertEqual((ctx.step_ids, ctx.team_ids), ({1}, {8}))
