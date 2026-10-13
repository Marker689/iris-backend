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
does not mention are left as they are. The business layer is mocked."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.tools import cases as cases_tools


_CASES = 'app.blueprints.rest.v2.mcp.tools.cases'


class TestCasesUpdateTool(TestCase):

    def _run(self, **args):
        case = SimpleNamespace(case_id=4, client_id=1, state_id=1, reviewer_id=None, owner_id=2)
        schema = MagicMock()
        schema.load.return_value = case
        schema.dump.return_value = {'case_id': 4}
        update = MagicMock(return_value=case)
        with patch(f'{_CASES}.cases_get_by_identifier', return_value=case), \
                patch(f'{_CASES}._case_schema', schema), \
                patch(f'{_CASES}.db'), \
                patch(f'{_CASES}.cases_update', update):
            cases_tools.iris_cases_update({'case_identifier': 4, 'payload': {}, **args})
        return update.call_args

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
