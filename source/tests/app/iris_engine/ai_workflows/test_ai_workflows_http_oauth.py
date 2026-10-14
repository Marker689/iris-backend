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

"""The `oauth2_client_credentials` auth of the HTTP request node: the
token is fetched with the client credentials, sent as a bearer, cached
until it expires, dropped on a 401, and neither it nor the client secret
is ever persisted. Runs on the in-memory engine harness."""

import base64
import json
from urllib.parse import parse_qs
from unittest.mock import patch

from app.iris_engine.ai_workflows import nodes
from app.iris_engine.webhooks.render import MASK
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import SECRET_NAME
from tests.app.iris_engine.ai_workflows.harness import SECRET_VALUE
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_TOKEN_URL = 'https://login.example.org/{{ vars.tenant }}/oauth2/v2.0/token'
_API_URL = 'https://graph.example.org/v1.0/security/runHuntingQuery'
_ACCESS_TOKEN = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.access-token-value'


def _http(**config):
    return {'id': 'http', 'type': 'http_request', 'config': {
        'method': 'POST', 'url': _API_URL, 'use_proxy': False, 'auth_type': 'oauth2_client_credentials',
        'oauth_token_url': _TOKEN_URL, 'oauth_client_id': 'client-1',
        'oauth_client_secret': f'{{{{ key("{SECRET_NAME}") }}}}', 'oauth_scope': 'https://graph.example.org/.default',
        'body_mode': 'template', 'body_template': '{"Query": "DeviceEvents | take 1"}', **config}}


def _tenant(value='tenant-1'):
    return {'id': 'vars', 'type': 'set_variables', 'config': {'variables': [{'name': 'tenant', 'value': value}]}}


class TestsHttpOAuth(EngineTestCase):

    def setUp(self):
        super().setUp()
        nodes._oauth_tokens.clear()
        self.addCleanup(nodes._oauth_tokens.clear)
        self.sent = []
        self.token_response = {'success': True, 'status_code': 200, 'response_headers': {}, 'response_body':
                               json.dumps({'token_type': 'Bearer', 'expires_in': 3599,
                                           'access_token': _ACCESS_TOKEN})}
        self.api_response = {'success': True, 'status_code': 200, 'response_headers': {},
                             'response_body': '{"results": []}'}
        patcher = patch('app.iris_engine.ai_workflows.nodes.webhooks_send', self._send)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _send(self, request, **kwargs):
        self.sent.append((request, kwargs))
        return dict(self.token_response if '/oauth2/' in request['url'] else self.api_response)

    def _run(self, *nodes_, **kwargs):
        return self.run_to_rest(workflow(chain(_tenant(), *nodes_)), **kwargs)

    def _token_requests(self):
        return [request for request, _kwargs in self.sent if '/oauth2/' in request['url']]

    def _api_requests(self):
        return [request for request, _kwargs in self.sent if request['url'] == _API_URL]

    def _persisted(self, run):
        return json.dumps([run.context, run.error, [(s.input, s.output, s.error) for s in self.steps(run)]],
                          default=str)

    def test_token_should_be_fetched_then_sent_as_bearer(self):
        run = self._run(_http())
        self.assertEqual('succeeded', run.status, run.error)
        [token_request] = self._token_requests()
        self.assertEqual(('POST', 'https://login.example.org/tenant-1/oauth2/v2.0/token'),
                         (token_request['method'], token_request['url']))
        self.assertEqual({'grant_type': ['client_credentials'], 'client_id': ['client-1'],
                          'client_secret': [SECRET_VALUE], 'scope': ['https://graph.example.org/.default']},
                         parse_qs(token_request['body']))
        self.assertEqual('application/x-www-form-urlencoded', token_request['headers']['Content-Type'])
        [api_request] = self._api_requests()
        self.assertEqual(f'Bearer {_ACCESS_TOKEN}', api_request['headers']['Authorization'])

    def test_token_and_client_secret_should_never_be_persisted(self):
        run = self._run(_http())
        persisted = self._persisted(run)
        self.assertNotIn(SECRET_VALUE, persisted)
        self.assertNotIn(_ACCESS_TOKEN, persisted)
        step = [s for s in self.steps(run) if s.node_id == 'http'][0]
        self.assertEqual({'token_url': 'https://login.example.org/tenant-1/oauth2/v2.0/token',
                          'client_id': 'client-1', 'scope': 'https://graph.example.org/.default',
                          'client_auth': 'body'}, step.input['oauth'])

    def test_client_secret_restrictions_should_not_outlive_the_token_request(self):
        run = self._run(_http())
        self.assertNotIn(SECRET_NAME, run.used_key_names or [])

    def test_token_should_be_reused_until_it_expires(self):
        self._run(_http())
        self._run(_http())
        self.assertEqual((1, 2), (len(self._token_requests()), len(self._api_requests())))

    def test_other_tenant_should_get_its_own_token(self):
        self._run(_http())
        self.run_to_rest(workflow(chain(_tenant('tenant-2'), _http())))
        self.assertEqual(['https://login.example.org/tenant-1/oauth2/v2.0/token',
                          'https://login.example.org/tenant-2/oauth2/v2.0/token'],
                         [r['url'] for r in self._token_requests()])

    def test_rejected_token_should_be_dropped(self):
        self.api_response = {'success': False, 'status_code': 401, 'response_headers': {}, 'response_body': '{}',
                             'error': 'HTTP 401'}
        self._run(_http())
        self.api_response = {'success': True, 'status_code': 200, 'response_headers': {}, 'response_body': '{}'}
        self._run(_http())
        self.assertEqual(2, len(self._token_requests()))

    def test_basic_client_auth_should_send_the_credentials_in_a_header(self):
        self._run(_http(oauth_client_auth='basic', oauth_client_id='client:1'))
        [token_request] = self._token_requests()
        self.assertEqual({'grant_type': ['client_credentials'], 'scope': ['https://graph.example.org/.default']},
                         parse_qs(token_request['body']))
        scheme, encoded = token_request['headers']['Authorization'].split(' ')
        self.assertEqual(('Basic', f'client%3A1:{SECRET_VALUE}'),
                         (scheme, base64.b64decode(encoded).decode('utf-8')))

    def test_token_endpoint_error_should_fail_without_calling_the_api(self):
        self.token_response = {'success': False, 'status_code': 400, 'response_headers': {}, 'error': 'HTTP 400',
                               'response_body': json.dumps({'error': 'invalid_client',
                                                            'error_description': 'AADSTS7000215: bad secret'})}
        run = self._run(_http())
        self.assertEqual('failed', run.status)
        self.assertEqual([], self._api_requests())
        self.assertIn('invalid_client', run.error)
        self.assertIn('AADSTS7000215', run.error)
        self.assertNotIn(SECRET_VALUE, self._persisted(run))

    def test_answer_without_access_token_should_fail(self):
        self.token_response = dict(self.token_response, response_body=json.dumps({'token_type': 'Bearer'}))
        run = self._run(_http())
        self.assertEqual(('failed', []), (run.status, self._api_requests()))

    def test_plain_http_token_url_should_be_refused(self):
        run = self._run(_http(oauth_token_url='http://login.example.org/{{ vars.tenant }}/token'))
        self.assertEqual(('failed', []), (run.status, self.sent))
        self.assertIn('https', run.error)

    def test_token_url_host_should_not_come_from_data(self):
        run = self._run(_http(oauth_token_url='https://{{ vars.tenant }}/token'))
        self.assertEqual(('failed', []), (run.status, self.sent))

    def test_dry_run_should_not_fetch_a_token(self):
        run = self._run(_http(), dry_run=True)
        self.assertEqual([], self.sent)
        step = [s for s in self.steps(run) if s.node_id == 'http'][0]
        self.assertEqual(MASK, step.output['request']['headers']['Authorization'])
