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

"""The Defender device timeline library workflow: it validates, its
scripts find the tenant, the window, the KQL of each page and the CSV,
and a run asks the analyst for the days then writes the pages of events
to the datastore with a token of the tenant of the customer."""

import datetime
import json
import os
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows import nodes
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_resume_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_trigger_config
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_key_references
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_literal_secrets
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_tools
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run_unconfined
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import FakeResolver
from tests.app.iris_engine.ai_workflows.harness import example_trigger
from tests.app.iris_engine.ai_workflows.harness import workflow

_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'app', 'iris_engine', 'ai_workflows',
                     'examples', 'defender_device_timeline.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_TENANT = '0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b'
_CLIENT_ID = '11111111-2222-3333-4444-555555555555'
_CLIENT_SECRET = 'mde-client-secret-value-0123456789'
_ACCESS_TOKEN = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.graph-token'
_NOW = '2026-10-14T12:30:00+00:00'
_CUSTOMER = {'customer_id': 4, 'customer_name': 'Contoso', 'customer_key_values': {'ENTRA_TENANT_ID': _TENANT}}
_TARGET = {'ok': True, 'reason': '', 'tenant': _TENANT, 'host': 'ws-001', 'customer': 'Contoso'}
_WINDOW = {'ok': True, 'reason': '', 'note': '', 'scope': 'network', 'tables': ['DeviceNetworkEvents'],
           'start': '2026-10-01T00:00:00Z', 'end': '2026-10-03T00:00:00Z',
           'label': '20261001T000000Z-20261003T000000Z'}


def _script(node_id, **inputs):
    outcome = ai_workflows_sandbox_run_unconfined(_NODES[node_id]['config']['code'], {'inputs': inputs},
                                                  max_steps=_NODES[node_id]['config']['max_steps'])
    return outcome['result'] if outcome['ok'] else outcome


def _prepare(customer=None, asset_name='WS-001'):
    return _script('prepare', customer=_CUSTOMER if customer is None else customer,
                   asset={'asset_id': 9, 'asset_name': asset_name})


def _window(start, end, scope=None, now=_NOW):
    return _script('window', answer={'start': start, 'end': end, 'scope': scope}, now=now)


def _query(last=None, window=None):
    return _script('query', target=_TARGET, window=window or _WINDOW, last=last, page_size=200)


def _event(ticks, report_id, **columns):
    return {'Timestamp': f'2026-10-01T00:00:{ticks % 60:02d}Z', 'Ticks': str(ticks),
            'Key': f'DeviceNetworkEvents:{report_id}', 'Table': 'DeviceNetworkEvents', 'DeviceName': 'ws-001',
            'ReportId': str(report_id), **columns}


def _page(results=None, body=None, query=None, max_passes=150):
    query = {'kql': '', 'cursor': '0', 'seen': [], 'page_size': 200, 'pass': 1, 'files': 0, 'total': 0,
             **(query or {})}
    response = {'status_code': 200, 'body': {'results': results or []} if body is None else body}
    return _script('page', response=response, query=query, target=_TARGET, window=_WINDOW, max_passes=max_passes)


class TestsDefenderTimelineDocument(TestCase):

    def test_workflow_should_validate(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(*example_trigger(body)))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['write_tool_allowlist']))

    def test_trigger_should_be_the_asset_menu(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        trigger_type, config = example_trigger(body)
        self.assertEqual(('manual', ['asset'], 'Pull Defender timeline'),
                         (trigger_type, config['menus'], config['menu_label']))

    def test_requirements_should_match_the_graph(self):
        graph_nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(graph_nodes))
        self.assertEqual({'keystore': sorted(ai_workflows_portable_key_references(graph_nodes)),
                          'tools': ai_workflows_portable_tools(graph_nodes, ['iris_case_datastore_files_create'])},
                         _DOCUMENT['requirements'])
        self.assertEqual(['MDE_CLIENT_ID', 'MDE_CLIENT_SECRET'], _DOCUMENT['requirements']['keystore'])

    def test_tenant_should_come_from_the_customer(self):
        hunt = _NODES['hunt']['config']
        self.assertEqual(('iris_customers_get', 'oauth2_client_credentials'),
                         (_NODES['customer']['config']['tool'], hunt['auth_type']))
        self.assertIn('{{ nodes.prepare.output.result.tenant }}', hunt['oauth_token_url'])


class TestsDefenderTimelinePrepareScript(TestCase):

    def test_tenant_and_device_should_be_found(self):
        self.assertEqual(_TARGET, _prepare())

    def test_tenant_domain_should_be_accepted(self):
        values = {'ENTRA_TENANT_ID': 'contoso.onmicrosoft.com'}
        self.assertTrue(_prepare(customer={**_CUSTOMER, 'customer_key_values': values})['ok'])

    def test_missing_tenant_should_say_where_to_add_it(self):
        result = _prepare(customer={'customer_name': 'Contoso', 'customer_key_values': None})
        self.assertFalse(result['ok'])
        self.assertIn('Contoso has no ENTRA_TENANT_ID key/value', result['reason'])

    def test_bad_tenant_should_be_refused(self):
        values = {'ENTRA_TENANT_ID': 'x/../evil'}
        result = _prepare(customer={**_CUSTOMER, 'customer_key_values': values})
        self.assertEqual((False, 'The ENTRA_TENANT_ID of Contoso is neither a tenant ID (a GUID) nor a domain.'),
                         (result['ok'], result['reason']))

    def test_asset_name_that_is_not_a_device_should_be_refused(self):
        result = _prepare(asset_name='ws-001"; DeviceEvents | take 1')
        self.assertFalse(result['ok'])
        self.assertIn('is not a device name', result['reason'])


class TestsDefenderTimelineWindowScript(TestCase):

    def test_days_should_be_included(self):
        result = _window('2026-10-01', '2026-10-02', 'network')
        self.assertEqual((True, '2026-10-01T00:00:00Z', '2026-10-03T00:00:00Z', ['DeviceNetworkEvents'],
                          '20261001T000000Z-20261003T000000Z'),
                         (result['ok'], result['start'], result['end'], result['tables'], result['label']))

    def test_times_should_be_kept(self):
        result = _window('2026-10-01 08:15', '2026-10-01T09:00:30Z')
        self.assertEqual(('2026-10-01T08:15:00Z', '2026-10-01T09:00:30Z', 7),
                         (result['start'], result['end'], len(result['tables'])))

    def test_window_should_be_cut_to_the_retention_and_now(self):
        result = _window('2026-08-01', '2026-12-31')
        self.assertEqual(('2026-09-14T12:30:00Z', '2026-10-14T12:30:00Z'), (result['start'], result['end']))
        self.assertIn('Defender keeps 30 days of events', result['note'])

    def test_bad_windows_should_be_refused(self):
        for start, end, scope, reason in (
                ('2026-10-02', '2026-10-01', None, 'The end of the window must be after its start.'),
                ('2026-02-30', '2026-03-01', None, 'The dates must be YYYY-MM-DD, or YYYY-MM-DD HH:MM (UTC).'),
                ('01/10/2026', '2026-10-02', None, 'The dates must be YYYY-MM-DD, or YYYY-MM-DD HH:MM (UTC).'),
                ('2026-10-20', '2026-10-21', None, 'The window is in the future.'),
                ('2026-01-01', '2026-01-02', None, 'The window is older than the 30 days of events Defender keeps.'),
                ('2026-10-01', '2026-10-02', 'alerts', 'Unknown event types alerts: choose one of all, process, '
                                                       'network, file, registry, logon, other.')):
            with self.subTest(start=start, end=end, scope=scope):
                result = _window(start, end, scope)
                self.assertEqual((False, reason, None), (result['ok'], result['reason'], result['start']))


class TestsDefenderTimelineQueryScript(TestCase):

    def test_first_page_should_start_at_the_window(self):
        result = _query()
        kql = result['kql']
        self.assertTrue(kql.startswith('let host = "ws-001";\nlet host_dot = strcat(host, ".");\n'
                                       'let window_start = datetime(2026-10-01T00:00:00Z);\n'
                                       'let window_end = datetime(2026-10-03T00:00:00Z);\nunion (DeviceNetworkEvents '))
        self.assertIn('| where Ticks >= 0\n| order by Ticks asc, Key asc\n| take 200\n', kql)
        self.assertNotIn('DeviceProcessEvents', kql)
        self.assertEqual(('0', [], 200, 1), (result['cursor'], result['seen'], result['page_size'], result['pass']))

    def test_next_page_should_skip_the_events_already_read(self):
        last = {'cursor': '638000', 'seen': ['DeviceNetworkEvents:7', 'DeviceNetworkEvents:8'], 'page_size': 100,
                'pass': 3, 'files': 2, 'total': 400}
        result = _query(last=last)
        self.assertIn('| where Ticks > 638000 or (Ticks == 638000 and Key !in '
                      '(dynamic(["DeviceNetworkEvents:7", "DeviceNetworkEvents:8"])))', result['kql'])
        self.assertIn('| take 100\n', result['kql'])
        self.assertEqual((4, 2, 400), (result['pass'], result['files'], result['total']))

    def test_tampered_cursor_should_fail(self):
        for last in ({'cursor': '1) | union (DeviceEvents'}, {'cursor': '1', 'seen': ['x"]) | union (DeviceEvents']}):
            with self.subTest(last=last):
                self.assertEqual('failed', _query(last=last)['kind'])


class TestsDefenderTimelinePageScript(TestCase):

    def test_events_should_become_csv(self):
        result = _page([_event(5, 1, RemoteIP='10.0.0.1', InitiatingProcessCommandLine='=cmd|"/c calc"!A0'),
                        _event(6, 2, RemoteUrl='a,b')])
        lines = result['csv'].split('\r\n')
        self.assertTrue(lines[0].startswith('Timestamp,Table,DeviceName,ActionType,'))
        self.assertIn(',"\'=cmd|""/c calc""!A0",10.0.0.1,', lines[1])
        self.assertIn(',"a,b",', lines[2])
        self.assertEqual(('', 2, 'ws-001_20261001T000000Z-20261003T000000Z_part-001.csv', True, False),
                         (lines[3], result['rows'], result['filename'], result['done'], result['more']))

    def test_full_page_should_continue_after_its_last_tick(self):
        results = [_event(5, 1), _event(9, 2), _event(9, 3)]
        result = _page(results, query={'page_size': 3})
        self.assertEqual(('9', ['DeviceNetworkEvents:2', 'DeviceNetworkEvents:3'], True, 1, 3),
                         (result['cursor'], result['seen'], result['more'], result['files'], result['total']))

    def test_page_at_the_same_tick_should_add_to_the_seen_events(self):
        result = _page([_event(9, 4), _event(9, 5)], query={'page_size': 2, 'cursor': '9',
                                                            'seen': ['DeviceNetworkEvents:2']})
        self.assertEqual(['DeviceNetworkEvents:2', 'DeviceNetworkEvents:4', 'DeviceNetworkEvents:5'], result['seen'])

    def test_cut_answer_should_ask_again_smaller(self):
        result = _page(body='{"results": [{"Timestamp": "2026-10-01T00:0', query={'cursor': '7', 'pass': 4})
        self.assertEqual((0, 100, '7', True, False), (result['rows'], result['page_size'], result['cursor'],
                                                      result['more'], result['done']))

    def test_cut_answer_of_a_tiny_page_should_fail(self):
        result = _page(body='{"results": [', query={'page_size': 10})
        self.assertEqual('failed', result['kind'])

    def test_last_pass_should_stop_with_a_truncated_timeline(self):
        result = _page([_event(5, 1), _event(6, 2)], query={'page_size': 2, 'pass': 150})
        self.assertEqual((True, False, False), (result['truncated'], result['more'], result['done']))


class _Resolver(FakeResolver):

    _VALUES = {'MDE_CLIENT_ID': _CLIENT_ID, 'MDE_CLIENT_SECRET': _CLIENT_SECRET}

    def get(self, name):
        if name not in self._VALUES:
            raise KeyError(name)
        self.used.add(name)
        if name == 'MDE_CLIENT_SECRET':
            self.ephemeral.add(_CLIENT_SECRET)
        return self._VALUES[name]


class TestsDefenderTimelineRun(EngineTestCase):

    maxDiff = None

    def setUp(self):
        super().setUp()
        self.resolver = _Resolver()
        nodes._oauth_tokens.clear()
        self.addCleanup(nodes._oauth_tokens.clear)
        self.sent = []
        self.pages = []
        self.notified = []
        self.tool_results['iris_customers_get'] = _CUSTOMER
        for target, replacement in (
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute',
                 lambda source, variables, max_steps=None, timeout_seconds=None:
                     ai_workflows_sandbox_run_unconfined(source, variables, max_steps=max_steps,
                                                         timeout_seconds=timeout_seconds)),
                ('app.iris_engine.ai_workflows.nodes.webhooks_send', self._send),
                ('app.iris_engine.ai_workflows.nodes.webhooks_db_proxies', lambda: None),
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access',
                 lambda _user_id, _type, _id: True),
                ('app.iris_engine.notifications.service.notify_many',
                 lambda users, *args, **kwargs: self.notified.append((list(users), args[1], kwargs.get('body'))))):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send(self, request, **_kwargs):
        self.sent.append(request)
        if '/oauth2/' in request['url']:
            return {'success': True, 'status_code': 200, 'response_headers': {}, 'response_body':
                    json.dumps({'token_type': 'Bearer', 'expires_in': 3599, 'access_token': _ACCESS_TOKEN})}
        results = self.pages.pop(0) if self.pages else []
        return {'success': True, 'status_code': 200, 'response_headers': {'Content-Type': 'application/json'},
                'response_body': json.dumps({'schema': [], 'results': results})}

    def _drain(self, run):
        for _ in range(200):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def _start(self, asset_name='WS-001'):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='manual', trigger_config=example_trigger(body)[1],
                      allowlist=body['write_tool_allowlist'])
        run = ai_workflows_engine_start_run(wf, 'manual', run_as_user_id=OWNER_ID, triggered_by_user_id=OWNER_ID,
                                            entity_type='case', entity_id=42,
                                            payload={'event': 'manual', 'data': {'asset_id': 9,
                                                                                 'asset_name': asset_name}})
        return self._drain(run)

    def _answer(self, run, **answer):
        [wait] = [w for w in self.store.waits.values() if w.status == 'pending']
        ai_workflows_engine_resume_wait(wait, {'answer': answer, 'answered_by': OWNER_ID}, resolved_by_id=OWNER_ID)
        return self._drain(run)

    def _writes(self):
        return [arguments for tool, arguments, _mode in self.executed if tool == 'iris_case_datastore_files_create']

    def _days(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        return str(today - datetime.timedelta(days=2)), str(today - datetime.timedelta(days=1))

    def test_run_should_write_every_page_to_the_datastore(self):
        self.pages = [[_event(n, n) for n in range(200)], [_event(500, 500), _event(501, 501)]]
        run = self._start()
        self.assertEqual(('waiting', 'ask'), (run.status, run.waiting_node_id))
        start, end = self._days()
        run = self._answer(run, start=start, end=end, scope='network')
        self.assertEqual('succeeded', run.status, run.error)
        # The case of the run and its customer are pinned
        self.assertEqual(('iris_customers_get', {'case_identifier': 42, 'customer_id': 1}), self.executed[0][:2])
        first, second = self._writes()
        # The header, then one line per event
        self.assertEqual(('Defender timelines', 'defender,timeline', 201, 3),
                         (first['folder'], first['tags'], first['content'].count('\r\n'),
                          second['content'].count('\r\n')))
        self.assertTrue(first['filename'].startswith('ws-001_') and first['filename'].endswith('_part-001.csv'))
        self.assertTrue(second['filename'].endswith('_part-002.csv'))
        self.assertIn('2 event(s) from', second['description'])
        [(users, title, body)] = self.notified
        self.assertEqual(([OWNER_ID], 'Defender timeline of ws-001: 202 event(s)'), (users, title))
        self.assertIn('202 event(s) from', body)

    def test_token_should_be_asked_to_the_tenant_of_the_customer_once(self):
        self.pages = [[_event(n, n) for n in range(200)], []]
        start, end = self._days()
        self._answer(self._start(), start=start, end=end)
        token_requests = [r for r in self.sent if '/oauth2/' in r['url']]
        hunts = [r for r in self.sent if r['url'] == 'https://graph.microsoft.com/v1.0/security/runHuntingQuery']
        self.assertEqual([f'https://login.microsoftonline.com/{_TENANT}/oauth2/v2.0/token'],
                         [r['url'] for r in token_requests])
        self.assertEqual(2, len(hunts))
        self.assertEqual(f'Bearer {_ACCESS_TOKEN}', hunts[0]['headers']['Authorization'])
        self.assertIn('let host = "ws-001";', json.loads(hunts[0]['body'])['Query'])
        self.assertIn('| where Ticks > 199 or (Ticks == 199 and Key !in', json.loads(hunts[1]['body'])['Query'])

    def test_secrets_should_never_be_persisted(self):
        self.pages = [[_event(1, 1)]]
        start, end = self._days()
        run = self._answer(self._start(), start=start, end=end)
        persisted = json.dumps([run.context, run.error, [(s.input, s.output, s.error) for s in self.steps(run)]],
                               default=str)
        self.assertNotIn(_CLIENT_SECRET, persisted)
        self.assertNotIn(_ACCESS_TOKEN, persisted)

    def test_customer_without_tenant_should_tell_the_analyst(self):
        self.tool_results['iris_customers_get'] = {**_CUSTOMER, 'customer_key_values': {}}
        run = self._start()
        self.assertEqual('failed', run.status)
        self.assertIn('Contoso has no ENTRA_TENANT_ID key/value', run.error)
        self.assertEqual('Defender timeline of ws-001 not pulled', self.notified[0][1])
        self.assertEqual(([], []), (self.sent, list(self.store.waits.values())))

    def test_empty_window_should_write_nothing(self):
        start, end = self._days()
        run = self._answer(self._start(), start=start, end=end)
        self.assertEqual(('succeeded', []), (run.status, self._writes()))

    def test_rejected_credentials_should_fail_with_a_hint(self):
        def _send(request, **_kwargs):
            self.sent.append(request)
            return {'success': False, 'status_code': 401, 'response_headers': {}, 'error': 'HTTP 401',
                    'response_body': json.dumps({'error': {'code': 'Unauthorized', 'message': 'Bad token'}})}
        self._send = _send
        patcher = patch('app.iris_engine.ai_workflows.nodes.webhooks_send', _send)
        patcher.start()
        self.addCleanup(patcher.stop)
        start, end = self._days()
        run = self._answer(self._start(), start=start, end=end)
        self.assertEqual('failed', run.status)
        self.assertIn('ThreatHunting.Read.All', run.error)
        self.assertEqual([], self._writes())
