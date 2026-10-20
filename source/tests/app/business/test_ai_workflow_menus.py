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

"""Manual workflows in the object menus: an entry per manual trigger
shown on the objects, naming its trigger node. DB helpers are patched."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.ai_workflow_menus import ai_workflow_menus_options
from app.business.ai_workflow_menus import ai_workflow_menus_run
from app.models.errors import BusinessProcessingError

_MENUS = 'app.business.ai_workflow_menus'


def _trigger(node_id, trigger_type, **config):
    return {'id': node_id, 'type': 'trigger', 'config': {'trigger_type': trigger_type, **config}}


def _workflow(workflow_id, name, *triggers, is_active=True):
    return SimpleNamespace(id=workflow_id, name=name, is_active=is_active, triggers=[], trigger_type=None,
                           trigger_config=None, graph={'nodes': list(triggers), 'edges': []})


class TestsMenuOptions(TestCase):

    def setUp(self):
        self.workflows = []
        patcher = patch(f'{_MENUS}.ai_workflows_db_list_active', lambda _type: self.workflows)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _options(self, hook_type='ioc'):
        return [(o['manual_hook_ui_name'], o['workflow_id'], o['workflow_trigger_id'])
                for o in ai_workflow_menus_options(hook_type)]

    def test_each_manual_trigger_on_the_objects_should_have_its_entry(self):
        self.workflows = [
            _workflow(1, 'Enrich',
                      _trigger('quick', 'manual', entity_types=['case'], menus=['ioc']),
                      _trigger('deep', 'manual', entity_types=['case'], menus=['ioc', 'asset'],
                               menu_label='Deep dive'),
                      _trigger('alerts', 'manual', entity_types=['alert'], menus=['alert']),
                      _trigger('hourly', 'cron', cron='0 * * * *')),
            _workflow(2, 'Off', _trigger('t', 'manual', entity_types=['case'], menus=['ioc']), is_active=False),
        ]
        self.assertEqual([('Enrich', 1, 'quick'), ('Deep dive', 1, 'deep')], self._options())
        self.assertEqual([('Deep dive', 1, 'deep')], self._options('asset'))
        self.assertEqual([('Enrich', 1, 'alerts')], self._options('alert'))

    def test_legacy_workflow_should_offer_its_trigger_node(self):
        workflow = _workflow(3, 'Legacy', {'id': 'trigger', 'type': 'trigger', 'config': {}})
        workflow.trigger_type, workflow.trigger_config = 'manual', {'entity_types': ['case'], 'menus': ['ioc']}
        self.workflows = [workflow]
        self.assertEqual([('Legacy', 3, 'trigger')], self._options())

    def test_run_should_refuse_a_malformed_trigger_id(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflow_menus_run(1, 'on_manual_trigger_ioc', [], 5, trigger_node_id=['quick'])


class TestsMenuRun(TestCase):

    def setUp(self):
        self.batch = False
        self.started = []
        for target, replacement in (
                (f'{_MENUS}.webhooks_current_actor', lambda: {'id': 5}),
                (f'{_MENUS}.webhooks_build_event', self._event),
                (f'{_MENUS}.ai_workflows_triggers_menu_batch', lambda *_args: self.batch),
                (f'{_MENUS}.ai_workflows_triggers_start_from_menu', self._start),
                (f'{_MENUS}.track_activity', lambda *_args, **_kwargs: None)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _event(_hook, data, **_kwargs):
        if isinstance(data, list):
            return {'object_type': 'asset', 'data': data, 'object_id': None, 'object_ids': data}
        return {'object_type': 'asset', 'data': data, 'object_id': data, 'object_ids': [data]}

    def _start(self, _workflow_id, event, _user_id, _scope_mask, trigger_node_id=None):
        self.started.append(event['data'])
        return SimpleNamespace(status='running', workflow_name='Thor', uuid='u'), None

    def test_each_object_should_get_its_run(self):
        self.assertEqual((2, []), ai_workflow_menus_run(1, 'on_manual_trigger_asset', [3, 8], 5, caseid=4))
        self.assertEqual([3, 8], self.started)

    def test_batch_should_start_one_run_on_all_the_objects(self):
        self.batch = True
        self.assertEqual((1, []), ai_workflow_menus_run(1, 'on_manual_trigger_asset', [3, 8], 5, caseid=4))
        self.assertEqual([[3, 8]], self.started)

    def test_batch_needs_the_case_of_the_objects(self):
        self.batch = True
        ai_workflow_menus_run(1, 'on_manual_trigger_alert', [3, 8], 5)
        self.assertEqual([3, 8], self.started)
