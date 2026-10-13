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

"""The advanced case filter: parsing shared by GET /cases/filter and the
iris_cases_filter MCP tool, and the tool's forwarding of it."""

from unittest import TestCase
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.dispatch import _augmented_input_schema
from app.blueprints.rest.v2.mcp.dispatch import _validate_args
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools.cases import iris_cases_filter
from app.business.cases import cases_filter_parse_advanced


def _owner(login):
    return {'fieldId': 'owner', 'operation': 'equals', 'value': login}


class TestsCasesFilterParseAdvanced(TestCase):

    def test_flat_list_should_be_wrapped_in_a_group_of_the_given_logic(self):
        self.assertEqual({'logic': 'or', 'items': [_owner('a'), _owner('b')]},
                         cases_filter_parse_advanced([_owner('a'), _owner('b')], 'or'))

    def test_nested_groups_should_be_kept(self):
        tree = {'logic': 'AND', 'items': [
            {'logic': 'or', 'items': [_owner('a'), _owner('b')]},
            {'fieldId': 'tags', 'operation': 'NOT_CONTAINS', 'value': 'red team'}]}
        self.assertEqual({'logic': 'and', 'items': [
            {'logic': 'or', 'items': [_owner('a'), _owner('b')]},
            {'fieldId': 'tags', 'operation': 'not_contains', 'value': 'red team'}]},
            cases_filter_parse_advanced(tree))

    def test_empty_operations_should_drop_the_value(self):
        self.assertEqual({'logic': 'and', 'items': [{'fieldId': 'outcome', 'operation': 'empty', 'value': ''}]},
                         cases_filter_parse_advanced([{'fieldId': 'outcome', 'operation': 'empty', 'value': 'x'}]))

    def test_invalid_filters_should_raise(self):
        for filters in ('owner=a',
                        [{'fieldId': 'owner', 'operation': 'like', 'value': 'a'}],
                        [{'fieldId': '', 'operation': 'equals', 'value': 'a'}],
                        [{'fieldId': 'owner', 'operation': 'equals', 'value': 3}],
                        {'logic': 'xor', 'items': []},
                        {'logic': 'and', 'items': 'nope'}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                cases_filter_parse_advanced(filters)

    def test_too_deep_nesting_should_raise(self):
        tree = _owner('a')
        for _ in range(10):
            tree = {'logic': 'and', 'items': [tree]}
        with self.assertRaisesRegex(ValueError, 'too deeply nested'):
            cases_filter_parse_advanced(tree)


class TestsIrisCasesFilterTool(TestCase):

    def setUp(self):
        patcher = patch('app.blueprints.rest.v2.mcp.tools.cases.cases_filter')
        self.cases_filter = patcher.start()
        self.addCleanup(patcher.stop)
        self.cases_filter.return_value.items = []
        self.cases_filter.return_value.total = 0
        self.cases_filter.return_value.pages = 0
        self.cases_filter.return_value.page = 1
        self.cases_filter.return_value.next_num = None

    def test_filters_should_be_forwarded_normalised(self):
        iris_cases_filter({'filters': {'logic': 'OR', 'items': [_owner('a')]}, 'is_open': True})
        kwargs = self.cases_filter.call_args.kwargs
        self.assertEqual({'logic': 'or', 'items': [_owner('a')]}, kwargs['advanced_filters'])
        self.assertTrue(kwargs['is_open'])

    def test_no_filters_should_forward_none(self):
        iris_cases_filter({})
        self.assertIsNone(self.cases_filter.call_args.kwargs['advanced_filters'])

    def test_invalid_filters_should_be_invalid_params(self):
        with self.assertRaises(MCPError):
            iris_cases_filter({'filters': {'items': [{'fieldId': 'owner', 'operation': 'like', 'value': 'a'}]}})
        self.cases_filter.assert_not_called()

    def test_dispatcher_should_accept_a_group_and_refuse_a_list(self):
        schema = _augmented_input_schema(TOOL_REGISTRY['iris_cases_filter'])
        self.assertIsNone(_validate_args({'filters': {'logic': 'and', 'items': [_owner('a')]}}, schema))
        self.assertIn('expected object', _validate_args({'filters': [_owner('a')]}, schema))
