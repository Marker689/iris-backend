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

"""Manual hooks of a war room: only `on_manual_trigger_war_room`, sent to
the modules, a webhook or a manual workflow. Dispatchers are patched."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.dim_hooks import invoke_hook_for_war_room
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.ai_workflows import MENU_ENTITY_TYPES
from app.models.errors import BusinessProcessingError

_DIM_HOOKS = 'app.business.dim_hooks'
_HOOK = 'on_manual_trigger_war_room'


class TestsWarRoomHooks(TestCase):

    def setUp(self):
        self.room = SimpleNamespace(war_room_id=3)
        self.calls = []
        for name in ('call_modules_hook', 'webhooks_invoke_manual'):
            patcher = patch(f'{_DIM_HOOKS}.{name}',
                            lambda *args, _name=name, **kwargs: self.calls.append((_name, args, kwargs)))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(f'{_DIM_HOOKS}.ai_workflow_menus_run', self._run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, workflow_id, hook_name, targets, user_id, scope_mask, trigger_node_id=None):
        self.calls.append(('ai_workflow_menus_run', (workflow_id, hook_name, targets, user_id, scope_mask),
                           {'trigger_node_id': trigger_node_id}))
        return 1, []

    def test_module_hook_should_get_the_war_room(self):
        result = invoke_hook_for_war_room(self.room, _HOOK, 'Enrich', 'iris_x_module')
        self.assertEqual(1, result.queued)
        self.assertEqual([('call_modules_hook', (_HOOK, [self.room]),
                           {'caseid': None, 'hook_ui_name': 'Enrich', 'module_name': 'iris_x_module'})],
                         self.calls)

    def test_webhook_should_get_the_war_room(self):
        invoke_hook_for_war_room(self.room, _HOOK, 'Send', 'IRIS webhooks', webhook_id=4)
        self.assertEqual([('webhooks_invoke_manual', (4, _HOOK, [self.room]), {})], self.calls)

    def test_workflow_should_run_on_the_war_room(self):
        result = invoke_hook_for_war_room(self.room, _HOOK, 'Triage', 'IRIS workflows', workflow_id=7,
                                          workflow_trigger_id='t1', user_id=5, scope_mask=None)
        self.assertEqual(1, result.queued)
        self.assertEqual([('ai_workflow_menus_run', (7, _HOOK, [self.room], 5, None), {'trigger_node_id': 't1'})],
                         self.calls)

    def test_other_hooks_should_be_refused(self):
        for hook_name in (None, '', 'on_manual_trigger_case', 'on_manual_trigger_alert'):
            with self.assertRaises(BusinessProcessingError):
                invoke_hook_for_war_room(self.room, hook_name, 'Enrich', 'iris_x_module')
        self.assertEqual([], self.calls)

    def test_war_room_menu_should_run_on_the_war_room(self):
        self.assertEqual(ENTITY_WAR_ROOM, MENU_ENTITY_TYPES['war_room'])
