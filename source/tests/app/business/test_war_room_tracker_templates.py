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

"""Unit tests for the tracker templates (no database)."""

import itertools
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_tracker_templates as templates
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import TrackerTemplate
from app.models.war_rooms import WarRoomTracker
from app.models.war_rooms import WarRoomTrackerStep


_BIZ = 'app.business.war_room_tracker_templates'


def _step(step_id, name, sort_order=0):
    return SimpleNamespace(id=step_id, name=name, description=None, color='red', icon='x', sort_order=sort_order)


def _template(**kwargs):
    fields = {'id': 1, 'name': 'Remediation', 'description': None,
              'steps': [{'name': 'Isolated', 'color': 'red', 'icon': None, 'description': None},
                        {'name': 'Restored', 'color': 'green', 'icon': None, 'description': None}],
              'trackers': [{'name': 'Assets', 'icon': None, 'description': None, 'source': 'assets',
                            'step_names': ['Restored'], 'columns': [], 'show_asset_flags': True,
                            'board_widgets': [{'type': 'tracker_count', 'title': 'Isolated',
                                               'config': {'step_names': ['isolated']}}]},
                           {'name': 'Todo', 'icon': None, 'description': None, 'source': 'manual',
                            'step_names': None, 'columns': [], 'show_asset_flags': False}],
              'created_by_id': None, 'created_at': None, 'updated_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class _Patches(TestCase):

    def setUp(self):
        self.added = []
        ids = itertools.count(100)

        def _add(obj):
            if getattr(obj, 'id', None) is None:
                obj.id = next(ids)
            self.added.append(obj)
            return obj

        self.patches = {
            'war_room_trackers_db_add': MagicMock(side_effect=_add),
            'war_room_trackers_db_commit': MagicMock(return_value=True),
            'war_room_trackers_db_rollback': MagicMock(),
            'war_room_trackers_db_delete': MagicMock(),
            'war_room_trackers_db_steps': MagicMock(return_value=[]),
            'war_room_trackers_db_trackers': MagicMock(return_value=[]),
            'war_room_trackers_db_trackers_by_ids': MagicMock(return_value=[]),
            'war_room_trackers_db_template_get': MagicMock(return_value=_template()),
            'war_room_trackers_db_template_find_by_name': MagicMock(return_value=None),
            'war_room_trackers_db_templates': MagicMock(return_value=[]),
            'war_room_trackers_db_user_names': MagicMock(return_value={}),
            'track_activity': MagicMock(),
            'war_room_board_widgets_append': MagicMock(return_value=0),
            'war_room_board_widgets_stored': MagicMock(return_value=None),
        }
        self._patchers = [patch(f'{_BIZ}.{name}', mock) for name, mock in self.patches.items()]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()


class TestContent(TestCase):

    def test_step_names_resolve_case_insensitively(self):
        steps, trackers = templates.tracker_templates_validate_content({
            'steps': [{'name': 'Isolated'}],
            'trackers': [{'name': 'T', 'source': 'assets', 'step_names': ['isolated', 'ISOLATED']}],
        })
        self.assertEqual(steps[0]['color'], 'slate')
        self.assertEqual(trackers[0]['step_names'], ['Isolated'])
        self.assertEqual(trackers[0]['columns'], [])
        self.assertEqual(trackers[0]['board_widgets'], [])

    def test_board_widgets_resolve_their_steps_by_name(self):
        _, trackers = templates.tracker_templates_validate_content({
            'steps': [{'name': 'Action required'}],
            'trackers': [{'name': 'T', 'source': 'customers',
                          'columns': [{'label': 'Sector', 'type': 'select', 'options': ['Bank']}],
                          'board_widgets': [
                              {'type': 'tracker_count', 'title': ' Action ', 'config': {'step_names': ['ACTION required']}},
                              {'type': 'tracker_progress', 'column': 'side', 'config': {'display': 'funnel'}},
                              {'type': 'tracker_breakdown', 'config': {'column_key': 'c_sector'}},
                              {'type': 'tracker_list', 'config': {
                                  'filter': {'column_key': 'c_sector', 'values': ['Bank']}}}]}],
        })
        count, progress, breakdown, listed = trackers[0]['board_widgets']
        self.assertEqual(count, {'type': 'tracker_count', 'title': 'Action', 'column': None,
                                 'config': {'step_names': ['Action required'], 'of_total': True, 'filter': None}})
        self.assertEqual(progress['config'], {'step_names': None, 'display': 'funnel', 'filter': None})
        self.assertEqual(progress['column'], 'side')
        self.assertEqual(breakdown['config'], {'step_names': None, 'column_key': 'c_sector'})
        self.assertEqual(listed['config']['step_names'], [])
        self.assertEqual(listed['config']['filter'], {'column_key': 'c_sector', 'values': ['Bank']})

    def test_invalid_content_is_refused(self):
        for data in ({'steps': [{'name': 'A'}, {'name': 'a'}]},
                     {'steps': [{'name': 'A', 'color': 'nope'}]},
                     {'trackers': [{'name': 'T', 'source': 'assets', 'step_names': ['Missing']}]},
                     {'trackers': [{'name': 'T', 'source': 'nope'}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'show_asset_flags': 'yes'}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'board_widgets': True}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'board_widgets': [{'type': 'cases'}]}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'board_widgets': [
                         {'type': 'tracker_count', 'config': {'step_names': ['Missing']}}]}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'board_widgets': [
                         {'type': 'tracker_breakdown', 'config': {'column_key': 'c_x'}}]}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'board_widgets': [
                         {'type': 'tracker_list', 'config': {'filter': {'column_key': 'c_x', 'values': ['a']}}}]}]},
                     {'trackers': [{'name': 'T', 'source': 'manual', 'board_widgets': [
                         {'type': 'tracker_progress', 'config': {'display': 'pie'}}]}]},
                     {'steps': 'x'}):
            with self.subTest(data=data):
                with self.assertRaises(BusinessProcessingError):
                    templates.tracker_templates_validate_content(data)

    def test_presets_are_valid(self):
        for preset in templates._PRESETS.values():
            steps, trackers = templates.tracker_templates_validate_content(preset)
            self.assertEqual(len(steps), 5)
            self.assertEqual(len(trackers), 1)

    def test_customer_notification_preset_comes_with_board_widgets(self):
        _, trackers = templates.tracker_templates_validate_content(templates._PRESETS['customer-notification'])
        widgets = trackers[0]['board_widgets']
        self.assertEqual([(w['type'], w['title']) for w in widgets], [
            ('tracker_count', 'Contacted'), ('tracker_count', 'Acknowledged'),
            ('tracker_count', 'Action required'), ('tracker_progress', 'Customer notification'),
            ('tracker_list', 'Customers with an action required')])
        self.assertEqual(widgets[0]['config']['step_names'],
                         ['Email sent', 'Acknowledged', 'Action required', 'Closed'])


class TestCrud(_Patches):

    def test_create_refuses_a_taken_name(self):
        self.patches['war_room_trackers_db_template_find_by_name'].return_value = _template()
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_create({'name': 'Remediation'}, 3)

    def test_create_from_preset(self):
        result = templates.tracker_templates_create_from_preset('customer-notification', 3)
        self.assertEqual(result['name'], 'Customer notification')
        self.assertEqual(len(result['steps']), 5)
        self.assertIsInstance(self.added[0], TrackerTemplate)
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_create_from_preset('nope', 3)

    def test_presets_listing(self):
        self.assertEqual([p['key'] for p in templates.tracker_templates_presets()],
                         ['customer-notification', 'asset-remediation'])

    def test_export_then_import(self):
        exported = templates.tracker_templates_export(1)
        self.assertEqual((exported['format'], exported['version']), ('iris-tracker-template', 1))
        exported['name'] = 'Copy'
        result = templates.tracker_templates_import(exported, 3)
        self.assertEqual(result['name'], 'Copy')
        self.assertEqual(len(result['trackers']), 2)
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_import({**exported, 'format': 'other'}, 3)

    def test_update_revalidates_the_trackers_against_new_steps(self):
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_update(1, {'steps': [{'name': 'Other'}]})

    def test_unknown_template(self):
        self.patches['war_room_trackers_db_template_get'].return_value = None
        with self.assertRaises(ObjectNotFoundError):
            templates.tracker_templates_get(9)


class TestApply(_Patches):

    def test_steps_are_merged_by_name_and_trackers_created(self):
        existing = _step(1, 'isolated', sort_order=3)
        self.patches['war_room_trackers_db_steps'].side_effect = [[existing], [existing, 'new']]
        steps, created = templates.tracker_templates_apply(7, 1, 3)
        new_steps = [obj for obj in self.added if isinstance(obj, WarRoomTrackerStep)]
        self.assertEqual([(s.name, s.color, s.sort_order) for s in new_steps], [('Restored', 'green', 4)])
        self.assertEqual(len(created), 2)
        self.assertTrue(all(isinstance(t, WarRoomTracker) for t in created))
        self.assertEqual(created[0].step_ids, [new_steps[0].id])
        self.assertIsNone(created[1].step_ids)
        self.assertEqual(steps, [existing, 'new'])
        self.patches['war_room_trackers_db_commit'].assert_called_once()
        (room_id, widgets), _ = self.patches['war_room_board_widgets_append'].call_args
        self.assertEqual(room_id, 7)
        widget, = widgets
        self.assertTrue(widget['id'].startswith('w_'))
        self.assertEqual((widget['type'], widget['title'], widget['column']), ('tracker_count', 'Isolated', None))
        self.assertEqual(widget['config'], {'tracker_id': created[0].id, 'step_ids': [existing.id],
                                            'of_total': True, 'filter': None})

    def test_an_invalid_board_rolls_back(self):
        self.patches['war_room_board_widgets_append'].side_effect = BusinessProcessingError('nope')
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_apply(7, 1, 3)
        self.patches['war_room_trackers_db_rollback'].assert_called_once()
        self.patches['war_room_trackers_db_commit'].assert_not_called()

    def test_room_limits_are_enforced(self):
        self.patches['war_room_trackers_db_trackers'].return_value = [SimpleNamespace(sort_order=0)] * 49
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_apply(7, 1, 3)


class TestSaveFromRoom(_Patches):

    def test_steps_ids_become_names(self):
        self.patches['war_room_trackers_db_steps'].return_value = [_step(1, 'Isolated'), _step(2, 'Restored')]
        self.patches['war_room_trackers_db_trackers'].return_value = [
            SimpleNamespace(id=4, name='Assets', icon=None, description=None, source='assets', step_ids=[2, 9],
                            columns=[], show_asset_flags=True, timeline_sync=None),
            SimpleNamespace(id=5, name='Todo', icon=None, description=None, source='manual', step_ids=None,
                            columns=[], show_asset_flags=False, timeline_sync=None)]
        self.patches['war_room_board_widgets_stored'].return_value = [
            {'id': 'cases', 'type': 'cases', 'title': None, 'column': 'main',
             'config': {'highlight_step_ids': [1], 'tracker_ids': None}},
            {'id': 'w_1', 'type': 'tracker_count', 'title': 'Isolated', 'column': None,
             'config': {'tracker_id': 4, 'step_ids': [1, 9], 'of_total': False, 'filter': None}},
            {'id': 'w_2', 'type': 'tracker_progress', 'title': None, 'column': 'side',
             'config': {'tracker_id': 4, 'step_ids': None, 'display': 'donut', 'filter': None}}]
        result = templates.tracker_templates_save_from_war_room(7, {'name': 'Mine'}, 3)
        self.assertEqual([s['name'] for s in result['steps']], ['Isolated', 'Restored'])
        self.assertEqual(result['trackers'][0]['step_names'], ['Restored'])
        self.assertEqual(result['trackers'][0]['board_widgets'], [
            {'type': 'tracker_count', 'title': 'Isolated', 'column': None,
             'config': {'step_names': ['Isolated'], 'of_total': False, 'filter': None}},
            {'type': 'tracker_progress', 'title': None, 'column': 'side',
             'config': {'step_names': None, 'display': 'donut', 'filter': None}}])
        self.assertEqual(result['trackers'][1]['board_widgets'], [])

    def test_unknown_tracker_ids_are_refused(self):
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_save_from_war_room(7, {'name': 'Mine', 'tracker_ids': [4]}, 3)
