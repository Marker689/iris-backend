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

"""The watchtowr hunt intake library workflow: it validates, its plan
matches the watchtowr tenants to IRIS customers and keeps, per asset,
only the CVEs watchtowr targeted on it, and a run creates one case per
customer with its assets and findings, a war room named after the hunt
tracking its CVEs, attaches the cases and notifies the room — and a
redelivered hunt creates nothing twice."""

import copy
import json
import os
from unittest import TestCase
from unittest.mock import patch

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
from tests.app.iris_engine.ai_workflows.harness import example_trigger
from tests.app.iris_engine.ai_workflows.harness import workflow

_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'app', 'iris_engine', 'ai_workflows',
                     'examples', 'watchtowr_hunt_intake.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_SETTINGS = {v['name']: v['value'] for v in _NODES['settings']['config']['variables']}

_PAYLOAD = {
    'schema_version': '1.0', 'event_id': 'tines-run-8f2c1a',
    'source': {'platform': 'watchtowr', 'parsed_by': 'tines'},
    'hunt': {'id': 'wt-hunt-2026-1042', 'name': 'Citrix NetScaler auth bypass',
             'url': 'https://app.watchtowr.com/hunts/wt-hunt-2026-1042', 'severity': 'critical',
             'tags': ['edge-device', 'citrix']},
    'vulnerabilities': [{'cve': 'CVE-2026-12346'}, {'cve': 'CVE-2026-12345', 'kev': True}],
    'customers': [{'name': 'ACME Corp', 'external_id': 'wt-tenant-77', 'assets': [
        {'name': 'vpn.acme.example', 'ip': '203.0.113.10', 'port': 443, 'service': 'Citrix NetScaler Gateway',
         'version': '14.1-21.57', 'cves': ['CVE-2026-12345']},
        {'name': 'vpn2.acme.example', 'ip': '203.0.113.11', 'port': 443}]}],
}
_ACME = {'customer_id': 4, 'customer_name': 'Acme Corporation', 'customer_description': '',
         'customer_key_values': {'WATCHTOWR_TENANT_ID': 'wt-tenant-77'}}
_GLOBEX = {'customer_id': 5, 'customer_name': 'Globex', 'customer_description': '', 'customer_key_values': {}}
_TAXONOMIES = {
    'severities': [{'id': 1, 'name': 'Unspecified'}, {'id': 4, 'name': 'Medium'}, {'id': 6, 'name': 'Critical'}],
    'asset_types': [{'id': 3, 'name': 'Linux - Server'}, {'id': 16, 'name': 'VPN'}, {'id': 2, 'name': 'Firewall'}],
}
_ROOM_ID = 31


def _script(node_id, **inputs):
    outcome = ai_workflows_sandbox_run_unconfined(_NODES[node_id]['config']['code'], {'inputs': inputs},
                                                  max_steps=_NODES[node_id]['config']['max_steps'])
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


def _plan(payload=None, customers=None, **settings):
    values = {**_SETTINGS, **settings}
    return _script('plan', payload=_PAYLOAD if payload is None else payload,
                   customers=[_ACME, _GLOBEX] if customers is None else customers, taxonomies=_TAXONOMIES,
                   tenant_field=values['tenant_field'], asset_types=values['asset_types'],
                   default_asset_type=values['default_asset_type'], untargeted_assets=values['untargeted_assets'],
                   case_tags=values['case_tags'], responders=values['responders'])


class TestsWatchtowrHuntDocument(TestCase):

    def test_workflow_should_validate(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(*example_trigger(body)))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['write_tool_allowlist']))

    def test_trigger_should_be_the_webhook(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual('webhook', example_trigger(body)[0])

    def test_requirements_should_match_the_graph(self):
        graph_nodes = list(_NODES.values())
        allowlist = _DOCUMENT['workflow']['write_tool_allowlist']
        self.assertEqual([], ai_workflows_portable_literal_secrets(graph_nodes))
        self.assertEqual([], ai_workflows_portable_key_references(graph_nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(graph_nodes, allowlist)},
                         _DOCUMENT['requirements'])

    def test_every_write_should_be_allowlisted(self):
        writes = {n['config']['tool'] for n in _NODES.values() if n['type'] == 'action'} - {
            'iris_customers_list', 'iris_taxonomies_list', 'iris_war_rooms_list', 'iris_cases_filter',
            'iris_case_assets_list'}
        self.assertEqual(writes, set(_DOCUMENT['workflow']['write_tool_allowlist']))

    def test_notification_should_be_about_the_war_room(self):
        self.assertEqual({'entity_type': 'war_room', 'entity_id': '{{ vars.war_room_id }}'},
                         _NODES['notify']['config']['about'])


class TestsWatchtowrHuntPlan(TestCase):

    maxDiff = None

    def test_tenant_should_be_matched_by_its_key_value(self):
        plan = _plan()
        self.assertTrue(plan['ok'], plan['reason'])
        [case] = plan['cases']
        self.assertEqual((4, '4', 'Acme Corporation', 'wt-hunt-2026-1042'),
                         (case['customer_id'], case['customer_id_text'], case['customer_name'], case['soc_id']))
        self.assertEqual('watchtowr - Citrix NetScaler auth bypass - Acme Corporation', case['case_name'])
        self.assertEqual('watchtowr,edge-device,citrix', case['case_tags'])
        self.assertEqual([], plan['unmatched'])

    def test_tenant_without_key_value_should_be_matched_by_name(self):
        acme = {**_ACME, 'customer_name': 'ACME Corp', 'customer_key_values': {}}
        [case] = _plan(customers=[acme, _GLOBEX])['cases']
        self.assertEqual(4, case['customer_id'])

    def test_unknown_tenant_should_be_reported_not_created(self):
        plan = _plan(customers=[_GLOBEX])
        self.assertFalse(plan['ok'])
        self.assertEqual([], plan['cases'])
        self.assertIn('ACME Corp (no IRIS customer has this name or WATCHTOWR_TENANT_ID = wt-tenant-77)',
                      plan['reason'])

    def test_ambiguous_tenant_should_be_reported(self):
        twin = {**_ACME, 'customer_id': 9, 'customer_name': 'Acme again'}
        plan = _plan(customers=[_ACME, twin])
        self.assertFalse(plan['ok'])
        self.assertIn('2 customers have WATCHTOWR_TENANT_ID = wt-tenant-77', plan['reason'])

    def test_each_asset_should_only_carry_its_targeted_cves(self):
        vpn, vpn2 = _plan()['cases'][0]['assets']
        self.assertEqual(('vpn.acme.example', 16, '203.0.113.10', ['CVE-2026-12345']),
                         (vpn['asset_name'], vpn['asset_type_id'], vpn['asset_ip'], vpn['cves']))
        # No service: the default type; no list: no CVE
        self.assertEqual(('vpn2.acme.example', 3, []), (vpn2['asset_name'], vpn2['asset_type_id'], vpn2['cves']))
        self.assertEqual({'ip': '203.0.113.10', 'port': '443', 'service': 'Citrix NetScaler Gateway',
                          'version': '14.1-21.57', 'cves': ['CVE-2026-12345']}, json.loads(vpn['asset_info']))
        self.assertIn('Citrix NetScaler Gateway 14.1-21.57 exposed on 203.0.113.10:443', vpn['asset_description'])

    def test_untargeted_assets_may_get_every_hunt_cve(self):
        vpn2 = _plan(untargeted_assets='hunt')['cases'][0]['assets'][1]
        self.assertEqual(['CVE-2026-12345', 'CVE-2026-12346'], vpn2['cves'])

    def test_hunt_cves_should_be_kev_first_with_a_note(self):
        plan = _plan()
        self.assertEqual([('CVE-2026-12345', True), ('CVE-2026-12346', False)],
                         [(c['cve'], c['kev']) for c in plan['cves']])
        self.assertEqual('CVE-2026-12345 (CISA KEV): watchtowr hunt Citrix NetScaler auth bypass '
                         'https://app.watchtowr.com/hunts/wt-hunt-2026-1042', plan['cves'][0]['note'])

    def test_severity_and_room_should_follow_the_hunt(self):
        plan = _plan()
        self.assertEqual(6, plan['severity_id'])
        self.assertEqual('Citrix NetScaler auth bypass', plan['room']['name'])
        self.assertEqual('[watchtowr:wt-hunt-2026-1042]', plan['room']['marker'])
        self.assertIn('In the CISA KEV catalogue: CVE-2026-12345', plan['room']['description'])
        self.assertEqual(4, _plan(payload={**_PAYLOAD, 'hunt': {**_PAYLOAD['hunt'], 'severity': 'odd'}})['severity_id'])

    def test_malformed_values_should_be_skipped(self):
        payload = copy.deepcopy(_PAYLOAD)
        payload['hunt']['url'] = 'javascript:alert(1)'
        payload['vulnerabilities'].append({'cve': 'not-a-cve'})
        payload['customers'][0]['assets'][0]['cves'].append('cve-2026-99999')
        payload['customers'][0]['assets'].append({'port': 22})
        payload['customers'][0]['assets'][1]['ip'] = '999.1.1.1'
        plan = _plan(payload=payload)
        self.assertEqual('', plan['hunt']['url'])
        vpn, vpn2 = plan['cases'][0]['assets']
        self.assertEqual(['CVE-2026-12345', 'CVE-2026-99999'], vpn['cves'])
        self.assertEqual('', vpn2['asset_ip'])
        self.assertEqual(['Acme Corporation: an asset with neither name nor IP', 'not a CVE: not-a-cve'],
                         plan['skipped'])

    def test_payload_without_hunt_should_be_refused(self):
        plan = _plan(payload={'customers': []})
        self.assertFalse(plan['ok'])
        self.assertIn('no hunt id or name', plan['reason'])

    def test_unknown_default_asset_type_should_be_refused(self):
        plan = _plan(default_asset_type='Toaster')
        self.assertFalse(plan['ok'])
        self.assertIn('The asset type Toaster does not exist', plan['reason'])

    def test_hunt_too_large_for_a_run_should_be_refused(self):
        payload = copy.deepcopy(_PAYLOAD)
        payload['customers'][0]['assets'] = [{'name': f'h{n}.acme.example', 'cves': ['CVE-2026-12345']}
                                             for n in range(150)]
        plan = _plan(payload=payload)
        self.assertFalse(plan['ok'])
        self.assertIn('too large for one run', plan['reason'])


class TestsWatchtowrHuntScripts(TestCase):

    def test_existing_assets_should_not_be_created_again(self):
        case = _plan()['cases'][0]
        missing = _script('to_create', case=case, existing=[{'asset_id': 1, 'asset_name': 'VPN.acme.example'}])
        self.assertEqual(['vpn2.acme.example'], [a['asset_name'] for a in missing])
        self.assertNotIn('cves', missing[0])
        self.assertNotIn('asset_ip', _script('to_create', case={'assets': [{**case['assets'][1], 'asset_ip': ''}]},
                                             existing=[])[0])

    def test_findings_should_only_hold_the_targeted_assets(self):
        case = _plan(untargeted_assets='none')['cases'][0]
        existing = [{'asset_id': 11, 'asset_name': 'vpn.acme.example', 'asset_type_id': 16},
                    {'asset_id': 12, 'asset_name': 'vpn2.acme.example', 'asset_type_id': 3}]
        self.assertEqual({'findings': [{'cve': 'CVE-2026-12345', 'asset_ids': [11]}], 'missing': []},
                         _script('findings', case=case, existing=existing))

    def test_findings_should_prefer_the_asset_of_the_planned_type(self):
        case = _plan()['cases'][0]
        existing = [{'asset_id': 11, 'asset_name': 'vpn.acme.example', 'asset_type_id': 2},
                    {'asset_id': 13, 'asset_name': 'vpn.acme.example', 'asset_type_id': 16}]
        self.assertEqual([13], _script('findings', case=case, existing=existing)['findings'][0]['asset_ids'])
        self.assertEqual(['vpn.acme.example'], _script('findings', case=case, existing=existing[:0])['missing'])

    def test_cursor_should_walk_a_list(self):
        self.assertEqual({'done': False, 'item': 'b'}, _script('cur_case', items=['a', 'b'], index='1'))
        self.assertEqual({'done': True, 'item': None}, _script('cur_case', items=['a', 'b'], index=2))

    def test_war_room_of_the_hunt_should_be_recognised_by_its_marker(self):
        marker = '[watchtowr:wt-hunt-2026-1042]'
        rooms = {'war_rooms': [
            {'war_room_id': 7, 'description': f'old {marker}', 'state': 'closed', 'archived_at': None},
            {'war_room_id': 9, 'description': 'Citrix NetScaler auth bypass', 'state': 'open', 'archived_at': None},
            {'war_room_id': 12, 'description': f'x {marker}', 'state': 'open', 'archived_at': None}]}
        self.assertEqual({'found': True, 'war_room_id': 12}, _script('pick_room', rooms=rooms, marker=marker))
        self.assertEqual({'found': False, 'war_room_id': None},
                         _script('pick_room', rooms={'war_rooms': rooms['war_rooms'][:2]}, marker=marker))


class TestsWatchtowrHuntRun(EngineTestCase):

    maxDiff = None

    def setUp(self):
        super().setUp()
        self.calls = []
        self.notified = []
        self.cases = []
        self.assets = []
        self.rooms = []
        self.created_rooms = set()
        self.customers = [_ACME, _GLOBEX]
        for target, replacement in (
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_tools_execute', self._execute),
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute',
                 lambda source, variables, max_steps=None, timeout_seconds=None:
                     ai_workflows_sandbox_run_unconfined(source, variables, max_steps=max_steps,
                                                         timeout_seconds=timeout_seconds)),
                ('app.iris_engine.ai_workflows.agent.ai_workflows_runtime_db_workflow_war_room_ids',
                 lambda _workflow_id, ids: {i for i in ids if i in self.created_rooms}),
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_db_entity_owner_ids',
                 lambda entity_type, entity_id: [OWNER_ID, 7] if (entity_type, entity_id) == ('war_room', _ROOM_ID)
                 else []),
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access',
                 lambda _user_id, _type, _id: True),
                ('app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user',
                 lambda _user_id: type('User', (), {'active': True})()),
                ('app.iris_engine.notifications.service.notify_many',
                 lambda users, *args, **kwargs: self.notified.append(
                     (list(users), args[1], kwargs.get('body'), kwargs.get('link'))))):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    # A small IRIS behind the tools
    def _execute(self, _user_id, tool_name, arguments, **kwargs):
        self.calls.append((tool_name, dict(arguments)))
        row = self.store.record_tool(tool_name, arguments, **kwargs)
        handler = getattr(self, f'_tool_{tool_name}', None)
        result = handler(arguments) if handler is not None else {}
        return {'ok': True, 'result': result, 'error': None, 'tool_call_id': row.id}

    def _tool_iris_customers_list(self, arguments):
        return {'customers': self.customers, 'page': arguments.get('page'), 'has_more': False}

    def _tool_iris_taxonomies_list(self, _arguments):
        return _TAXONOMIES

    def _tool_iris_war_rooms_list(self, arguments):
        return {'war_rooms': [r for r in self.rooms if arguments['search'] in r['description']]}

    def _tool_iris_war_rooms_create(self, arguments):
        room = {'war_room_id': _ROOM_ID + len(self.rooms), 'name': arguments['name'],
                'description': arguments['description'], 'state': arguments['state'],
                'severity_id': arguments['severity_id'], 'archived_at': None}
        self.rooms.append(room)
        self.created_rooms.add(room['war_room_id'])
        return room

    def _tool_iris_cases_filter(self, arguments):
        found = [c for c in self.cases if c['soc_id'] == arguments['case_soc_id']
                 and str(c['client_id']) == arguments['case_customer_id']]
        return {'total': len(found), 'data': found[:1], 'last_page': 1, 'current_page': 1, 'next_page': None}

    def _tool_iris_cases_create(self, arguments):
        payload = arguments['payload']
        case = {'case_id': 120 + len(self.cases), 'name': payload['case_name'], 'soc_id': payload['case_soc_id'],
                'client_id': payload['case_customer_id']}
        self.cases.append(case)
        return case

    def _tool_iris_case_assets_list(self, arguments):
        rows = [a for a in self.assets if a['case_id'] == arguments['case_identifier']]
        return {'total': len(rows), 'data': rows, 'last_page': 1, 'current_page': 1, 'next_page': None}

    def _tool_iris_case_assets_create(self, arguments):
        asset = {'asset_id': 300 + len(self.assets), 'case_id': arguments['case_identifier'],
                 **arguments['payload']}
        self.assets.append(asset)
        return asset

    def _writes(self, tool_name):
        return [arguments for name, arguments in self.calls if name == tool_name]

    def _run(self, payload=None, responders=None):
        document = copy.deepcopy(_DOCUMENT)
        if responders is not None:
            for node in document['workflow']['graph']['nodes']:
                if node['id'] == 'settings':
                    for variable in node['config']['variables']:
                        if variable['name'] == 'responders':
                            variable['value'] = responders
        body = ai_workflows_portable_read_workflow(document)
        wf = workflow(body['graph'], trigger_type='webhook', trigger_config=example_trigger(body)[1],
                      allowlist=body['write_tool_allowlist'])
        run = ai_workflows_engine_start_run(wf, 'webhook', run_as_user_id=OWNER_ID,
                                            payload=copy.deepcopy(payload or _PAYLOAD))
        for _ in range(200):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def test_run_should_create_the_case_assets_findings_and_war_room(self):
        run = self._run(responders=[7])
        self.assertEqual('succeeded', run.status, run.error)
        [room] = self._writes('iris_war_rooms_create')
        self.assertEqual(('Citrix NetScaler auth bypass', 'open', 6), (room['name'], room['state'],
                                                                       room['severity_id']))
        self.assertEqual([('CVE-2026-12345', _ROOM_ID), ('CVE-2026-12346', _ROOM_ID)],
                         [(a['identifier'], a['war_room_id']) for a in
                          self._writes('iris_war_room_vulnerabilities_track')])
        [case] = self._writes('iris_cases_create')
        self.assertEqual((4, 'wt-hunt-2026-1042'), (case['payload']['case_customer_id'],
                                                    case['payload']['case_soc_id']))
        self.assertEqual([{'case_identifier': 120, 'payload': {'severity_id': 6}}],
                         self._writes('iris_cases_update'))
        self.assertEqual([(_ROOM_ID, 120)], [(a['war_room_id'], a['case_id'])
                                             for a in self._writes('iris_war_room_cases_attach')])
        self.assertEqual([('vpn.acme.example', 16), ('vpn2.acme.example', 3)],
                         [(a['payload']['asset_name'], a['payload']['asset_type_id'])
                          for a in self._writes('iris_case_assets_create')])
        # Only the asset watchtowr targeted carries the CVE
        self.assertEqual([{'case_identifier': 120, 'identifier': 'CVE-2026-12345', 'asset_ids': [300]}],
                         self._writes('iris_case_vulnerabilities_create'))
        self.assertEqual([{'war_room_id': _ROOM_ID, 'user_id': 7, 'role': 'responder', 'access': 'full_access'}],
                         self._writes('iris_war_room_members_add'))
        [(users, title, body, link)] = self.notified
        self.assertEqual(([OWNER_ID, 7], 'watchtowr hunt Citrix NetScaler auth bypass: 1 case(s)',
                          f'/war-rooms/{_ROOM_ID}'), (users, title, link))
        self.assertIn('Acme Corporation: case #120 created, 2 of 2 asset(s) added, 1 targeted CVE finding(s)', body)

    def test_redelivered_hunt_should_create_nothing_twice(self):
        self._run()
        self.calls = []
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        for tool_name in ('iris_war_rooms_create', 'iris_cases_create', 'iris_cases_update',
                          'iris_case_assets_create'):
            self.assertEqual([], self._writes(tool_name), tool_name)
        self.assertEqual([(_ROOM_ID, 120)], [(a['war_room_id'], a['case_id'])
                                             for a in self._writes('iris_war_room_cases_attach')])
        self.assertEqual([[300]], [a['asset_ids'] for a in self._writes('iris_case_vulnerabilities_create')])
        self.assertIn('case #120 updated, 0 of 2 asset(s) added', self.notified[-1][2])

    def test_unknown_customer_should_only_tell_the_owner(self):
        self.customers = [_GLOBEX]
        run = self._run()
        self.assertEqual('failed', run.status)
        self.assertIn('None of the 1 watchtowr customer(s)', run.error)
        self.assertEqual(['iris_customers_list', 'iris_taxonomies_list'], [name for name, _ in self.calls])
        [(users, title, _body, _link)] = self.notified
        self.assertEqual(([OWNER_ID], 'watchtowr hunt Citrix NetScaler auth bypass not handled'), (users, title))

    def test_customers_should_be_paged(self):
        pages = [{'customers': [_GLOBEX], 'page': 1, 'has_more': True},
                 {'customers': [_ACME], 'page': 2, 'has_more': False}]
        self._tool_iris_customers_list = lambda arguments: pages[arguments['page'] - 1]
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual([1, 2], [a['page'] for a in self._writes('iris_customers_list')])
        self.assertEqual(4, self._writes('iris_cases_create')[0]['payload']['case_customer_id'])
