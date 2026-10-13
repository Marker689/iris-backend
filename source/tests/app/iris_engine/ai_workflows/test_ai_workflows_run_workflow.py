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

"""One workflow calling another: the `run_workflow` node, the caller
resumed when the called run ends, and the start of the called run (its
identity, chain and refusals)."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app.iris_engine.ai_workflows import engine
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_expire_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_start_from_workflow
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow
from tests.app.iris_engine.ai_workflows.test_ai_workflows_triggers import patch_skip_counter

_TRIGGERS = 'app.iris_engine.ai_workflows.triggers'
_ENGINE = 'app.iris_engine.ai_workflows.engine'


def _call(mode='wait', **config):
    return {'id': 'call', 'type': 'run_workflow', 'label': 'Enrich',
            'config': {'workflow_id': 5, 'mode': mode, 'timeout_minutes': 30,
                       'inputs': [{'name': 'title', 'value': '{{ entity.title }}'}], **config}}


class TestsRunWorkflowNode(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.calls = []
        self.refusal = None
        store = self.store

        def _start(caller, node_id, workflow_id, inputs, wait_uuid=None):
            self.calls.append((caller.id, node_id, workflow_id, inputs, wait_uuid))
            if self.refusal:
                return None, self.refusal
            return SimpleNamespace(uuid=uuid.uuid4(), workflow_id=workflow_id, workflow_name='Enrich',
                                   status='running'), None

        def _wait_by_uuid(wait_uuid):
            return next((w for w in store.waits.values() if str(w.uuid) == str(wait_uuid)), None)

        for target, replacement in ((f'{_TRIGGERS}.ai_workflows_triggers_start_from_workflow', _start),
                                    (f'{_ENGINE}.ai_workflows_db_get_wait_by_uuid', _wait_by_uuid)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _graph(error_port=False):
        graph = chain(_call(), {'id': 'ok', 'type': 'stop', 'config': {}})
        if error_port:
            graph['nodes'].append({'id': 'handled', 'type': 'stop', 'config': {'reason': 'handled'}})
            graph['edges'].append({'id': 'err', 'source': 'call', 'target': 'handled', 'source_port': 'error'})
        return graph

    def _parked(self, error_port=False):
        run = self.run_to_rest(workflow(self._graph(error_port)))
        self.assertEqual('waiting', run.status)
        waits = list(self.store.waits.values())
        self.assertEqual(['workflow'], [w.kind for w in waits])
        return run, waits[0]

    @staticmethod
    def _child(parent, wait, status='succeeded', error=None):
        return SimpleNamespace(
            id=500, uuid=uuid.uuid4(), workflow_id=5, workflow_name='Enrich', status=status, error=error,
            parent_run_id=parent.id,
            context={'caller_wait': str(wait.uuid), 'vars': {'score': 3}, 'last': 'end',
                     'nodes': {'end': {'output': {'verdict': 'benign'}}}})

    def _resume(self, child):
        engine._resume_caller(child)
        while self.enqueued:
            ai_workflows_engine_step(self.enqueued.pop())

    def test_wait_mode_should_start_the_workflow_and_park(self):
        run, wait = self._parked()
        self.assertEqual([(run.id, 'call', 5, {'title': 'Phishing'}, wait.uuid)], self.calls)
        self.assertEqual('call', run.waiting_node_id)
        self.assertEqual('Enrich', self.steps(run)[1].output['workflow_name'])

    def test_succeeded_run_should_resume_the_caller_through_out(self):
        run, wait = self._parked()
        self._resume(self._child(run, wait))
        self.assertEqual('resolved', wait.status)
        self.assertEqual('succeeded', run.status)
        resumed = self.steps(run)[2]
        self.assertEqual('out', resumed.port)
        self.assertEqual(({'score': 3}, {'verdict': 'benign'}, 'succeeded'),
                         (resumed.output['vars'], resumed.output['output'], resumed.output['status']))

    def test_failed_run_should_follow_the_error_port_when_wired(self):
        run, wait = self._parked(error_port=True)
        self._resume(self._child(run, wait, status='failed', error='boom'))
        self.assertEqual('error', self.steps(run)[2].port)
        self.assertEqual('boom', self.steps(run)[2].output['error'])
        self.assertEqual('handled', self.steps(run)[-1].node_id)

    def test_failed_run_should_fail_the_caller_without_an_error_port(self):
        run, wait = self._parked()
        self._resume(self._child(run, wait, status='failed', error='boom'))
        self.assertEqual(('failed', 'Enrich: the called workflow Enrich failed: boom'), (run.status, run.error))
        self.assertEqual('resolved', wait.status)
        self.assertIn(run, self.published)

    def test_run_of_another_parent_should_not_resume_the_wait(self):
        run, wait = self._parked()
        child = self._child(run, wait)
        child.parent_run_id = run.id + 1000
        self._resume(child)
        child = self._child(run, wait)
        child.parent_run_id = None
        self._resume(child)
        self.assertEqual(('pending', 'waiting'), (wait.status, run.status))

    def test_expired_wait_should_leave_through_timeout(self):
        run, wait = self._parked()
        ai_workflows_engine_expire_wait(wait)
        while self.enqueued:
            ai_workflows_engine_step(self.enqueued.pop())
        resumed = self.steps(run)[2]
        self.assertEqual(('expired', 'timeout'), (wait.status, resumed.port))
        self.assertTrue(resumed.output['timed_out'])
        self.assertEqual('Enrich', resumed.output['workflow_name'])

    def test_async_mode_should_not_wait(self):
        run = self.run_to_rest(workflow(chain(_call(mode='async'), {'id': 'ok', 'type': 'stop', 'config': {}})))
        self.assertEqual('succeeded', run.status)
        self.assertIsNone(self.calls[0][4])
        self.assertEqual({}, self.store.waits)
        self.assertEqual('running', self.steps(run)[1].output['status'])

    def test_dry_run_should_start_nothing(self):
        run = self.run_to_rest(workflow(self._graph()), dry_run=True)
        self.assertEqual('succeeded', run.status)
        self.assertEqual([], self.calls)
        self.assertEqual({'dry_run': True, 'workflow_id': 5, 'mode': 'wait', 'inputs': {'title': 'Phishing'}},
                         self.steps(run)[1].output)

    def test_refused_start_should_fail_the_node_and_drop_the_wait(self):
        self.refusal = 'Chain depth limit reached'
        run = self.run_to_rest(workflow(self._graph()))
        self.assertEqual('failed', run.status)
        self.assertIn('Chain depth limit reached', run.error)
        self.assertEqual(['cancelled'], [w.status for w in self.store.waits.values()])


class TestsStartFromWorkflow(EngineTestCase):
    """The called run acts as the caller, one level deeper."""

    ANALYST_ID = 7

    def setUp(self):
        super().setUp()
        self.called = workflow(chain(), trigger_type='workflow', workflow_id=5, name='Enrich', skipped_count=0,
                               last_skip_reason=None, last_skipped_at=None)
        self.runs_last_hour = 0
        self.admins = set()
        self.can_access = {OWNER_ID: True, self.ANALYST_ID: True}
        users = {OWNER_ID: SimpleNamespace(id=OWNER_ID, active=True, user='owner'),
                 self.ANALYST_ID: SimpleNamespace(id=self.ANALYST_ID, active=True, user='analyst'),
                 999: SimpleNamespace(id=999, active=True, user='other')}
        for target, replacement in (
                (f'{_TRIGGERS}.ai_workflows_db_get', lambda _id: self.called if _id == 5 else None),
                (f'{_TRIGGERS}.ai_workflows_db_lock_workflow', lambda _id: self.called),
                (f'{_TRIGGERS}.ai_workflows_db_commit', lambda: None),
                (f'{_TRIGGERS}.ai_workflows_db_count_runs_since', lambda *_args: self.runs_last_hour),
                (f'{_TRIGGERS}.ai_workflows_db_get_user', users.get),
                (f'{_TRIGGERS}.ac_get_effective_permissions_of_user', lambda user: user.id),
                (f'{_TRIGGERS}.ac_has_permission_server_administrator', lambda user_id: user_id in self.admins),
                (f'{_ENGINE}.ai_workflows_db_get_user', users.get),
                (f'{_ENGINE}.ai_workflows_entities_user_can_access',
                 lambda user_id, _type, _id: self.can_access.get(user_id, False))):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patch_skip_counter(self, lambda _id: self.called)
        self.caller = SimpleNamespace(
            id=70, uuid=uuid.uuid4(), workflow_id=3, workflow_name='Triage', owner_id=OWNER_ID,
            run_as_user_id=self.ANALYST_ID, triggered_by_user_id=self.ANALYST_ID, scope_mask=None,
            entity_type='case', entity_id=42, sub_entity={'type': 'ioc', 'id': 9}, chain_depth=0)

    def _start(self, workflow_id=5, wait_uuid=None):
        return ai_workflows_triggers_start_from_workflow(self.caller, 'call', workflow_id, {'ioc': '1.2.3.4'},
                                                         wait_uuid=wait_uuid)

    def test_called_run_should_act_as_the_caller_one_level_deeper(self):
        wait_uuid = uuid.uuid4()
        run, reason = self._start(wait_uuid=wait_uuid)
        self.assertIsNone(reason)
        self.assertEqual(('running', 'workflow', self.ANALYST_ID, self.ANALYST_ID, OWNER_ID),
                         (run.status, run.trigger_type, run.run_as_user_id, run.triggered_by_user_id, run.owner_id))
        self.assertEqual(('case', 42, {'type': 'ioc', 'id': 9}, 1, 70),
                         (run.entity_type, run.entity_id, run.sub_entity, run.chain_depth, run.parent_run_id))
        self.assertEqual({'inputs': {'ioc': '1.2.3.4'},
                          'caller': {'run_uuid': str(self.caller.uuid), 'workflow_id': 3,
                                     'workflow_name': 'Triage', 'node_id': 'call'}}, run.trigger_payload)
        self.assertEqual(str(wait_uuid), run.context['caller_wait'])
        self.assertEqual([run.id], self.enqueued)

    def test_async_call_should_not_link_a_wait(self):
        run, _reason = self._start()
        self.assertNotIn('caller_wait', run.context)

    def test_workflow_without_a_workflow_trigger_should_not_start(self):
        expected = (None, 'The workflow does not exist, is not active, has no "Called by a workflow" trigger or '
                          'belongs to another user')
        self.assertEqual(expected, self._start(workflow_id=6))
        self.called.is_active = False
        self.assertEqual(expected, self._start())
        self.called.is_active = True
        self.called.trigger_type = 'manual'
        self.assertEqual(expected, self._start())
        self.called.triggers = [{'type': 'manual', 'config': {}}, {'type': 'workflow', 'config': {}}]
        self.assertEqual('running', self._start()[0].status)

    def test_workflow_of_another_owner_needs_an_admin_caller(self):
        self.called.owner_id = 999
        self.assertIsNone(self._start()[0])
        self.assertEqual({}, self.store.runs)
        self.admins.add(OWNER_ID)
        self.assertEqual('running', self._start()[0].status)

    def test_chain_depth_should_bound_calls(self):
        self.caller.chain_depth = 2
        run, reason = self._start()
        self.assertIsNone(run)
        self.assertIn('Chain depth limit reached', reason)
        self.assertEqual(1, self.called.skipped_count)

    def test_caller_without_access_should_record_a_skipped_run(self):
        self.can_access = {OWNER_ID: True, self.ANALYST_ID: False}
        run, reason = self._start()
        self.assertEqual(('skipped', self.ANALYST_ID), (run.status, run.run_as_user_id))
        self.assertIn('cannot access case #42', reason)
        self.assertEqual([], self.enqueued)

    def test_hourly_cap_should_hold(self):
        self.called.max_runs_per_hour = 1
        self.runs_last_hour = 1
        self.assertEqual((None, 'Hourly run limit reached'), self._start())
