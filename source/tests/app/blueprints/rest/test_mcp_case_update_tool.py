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

"""Unit tests for `iris_cases_update`: tags and protagonists the call
does not mention are left as they are, and a new summary reaches the
collaborative document. The business layer is mocked."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools import cases as cases_tools


_CASES = 'app.blueprints.rest.v2.mcp.tools.cases'


class TestCasesUpdateTool(TestCase):

    def _run(self, payload=None, assignee_has_access=True, **args):
        case = SimpleNamespace(case_id=4, client_id=1, state_id=1, reviewer_id=None, owner_id=2,
                               description=(payload or {}).get('case_description', 'old summary'))
        self.schema = MagicMock()
        self.schema.load.return_value = case
        self.schema.dump.return_value = {'case_id': 4}
        update = MagicMock(return_value=case)
        self.collab_replace = MagicMock()
        with patch(f'{_CASES}.collab_replace_markdown', self.collab_replace), \
                patch(f'{_CASES}.cases_get_by_identifier', return_value=case), \
                patch(f'{_CASES}._case_schema', self.schema), \
                patch(f'{_CASES}.db'), \
                patch(f'{_CASES}.ac_fast_check_user_has_case_access', return_value=assignee_has_access), \
                patch(f'{_CASES}.cases_update', update):
            cases_tools.iris_cases_update({'case_identifier': 4, 'payload': payload or {}, **args})
        return update.call_args

    def _loaded_payload(self):
        return self.schema.load.call_args.args[0]

    def test_partial_payload_keeps_the_current_customer(self):
        self._run(payload={'case_description': 'post-mortem'})
        self.assertEqual({'case_description': 'post-mortem', 'case_customer_id': 1}, self._loaded_payload())

    def test_case_name_keeps_its_identifier_prefix(self):
        self._run(payload={'case_name': '#4 - renamed'})
        self.assertEqual('#4 - renamed', self._loaded_payload()['case_name'])

    def test_assignee_without_case_access_is_refused_before_the_load(self):
        with self.assertRaises(MCPError):
            self._run(payload={'owner_id': 9}, assignee_has_access=False)
        self.schema.load.assert_not_called()

    def test_tags_and_protagonists_given_in_payload_are_applied(self):
        call = self._run(payload={'case_tags': 'apt', 'protagonists': []})
        self.assertEqual([], call.args[2])
        self.assertEqual('apt', call.args[3])

    def test_soc_id_is_advertised_as_a_string(self):
        properties = TOOL_REGISTRY['iris_cases_update'].input_schema['properties']['payload']['properties']
        self.assertEqual('string', properties['case_soc_id']['type'])

    def test_omitted_tags_and_protagonists_are_kept(self):
        call = self._run()
        self.assertIsNone(call.args[2])
        self.assertIsNone(call.args[3])

    def test_given_tags_and_protagonists_replace_them(self):
        protagonists = [{'role': 'Analyst', 'name': 'jdoe'}]
        call = self._run(tags='restricted,restricted_by_wf', protagonists=protagonists)
        self.assertEqual(protagonists, call.args[2])
        self.assertEqual('restricted,restricted_by_wf', call.args[3])

    def test_empty_values_still_clear_them(self):
        call = self._run(tags='', protagonists=[])
        self.assertEqual([], call.args[2])
        self.assertEqual('', call.args[3])

    def test_new_summary_is_pushed_into_the_collaborative_document(self):
        self._run(payload={'case_description': 'post-mortem'})
        self.collab_replace.assert_called_once_with('case-summary:4', 'post-mortem')

    def test_update_without_summary_leaves_the_collaborative_document_alone(self):
        self._run(payload={'case_name': '#4 - renamed'})
        self.collab_replace.assert_not_called()


class TestCasesGetTool(TestCase):

    def test_summary_is_read_from_the_collaborative_document(self):
        case = SimpleNamespace(case_id=4)
        schema = MagicMock()
        schema.dump.return_value = {'case_id': 4, 'case_description': 'stale column'}
        with patch(f'{_CASES}.cases_get_by_identifier', return_value=case), \
                patch(f'{_CASES}._case_schema', schema), \
                patch(f'{_CASES}.collab_current_markdown', return_value='live summary') as current:
            data = cases_tools.iris_cases_get({'case_identifier': 4})
        current.assert_called_once_with('case-summary:4', 'stale column')
        self.assertEqual('live summary', data['case_description'])
