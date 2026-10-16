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

"""Unit tests for the war-room scope MCP tools: the business layer only
ever sees the attached cases the caller can read and, for writes, the
subset it has full access on. The business layer is mocked."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp import classification
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools import war_room_scope as scope_tools


_SCOPE = 'app.blueprints.rest.v2.mcp.tools.war_room_scope'


def _user():
    user = MagicMock()
    user._get_current_object.return_value = SimpleNamespace(id=5)
    return user


class _ScopeTest(TestCase):

    def setUp(self):
        # Cases 1, 2 and 3 are attached and readable; 2 is full access.
        patches = [
            patch(f'{_SCOPE}.iris_current_user', _user()),
            patch(f'{_SCOPE}.war_room_readable_attached_case_ids', return_value=[1, 2, 3]),
            patch(f'{_SCOPE}.ac_fast_check_current_user_has_cases_access', return_value=[2]),
            patch(f'{_SCOPE}.ac_current_user_can_read_vulnerabilities', return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestScopeLists(_ScopeTest):

    def test_assets_list_covers_the_readable_cases_one_page_at_a_time(self):
        with patch(f'{_SCOPE}.war_room_scope_list_assets', return_value={}) as list_assets:
            scope_tools.iris_war_room_scope_assets_list({'war_room_id': 3, 'q': 'dc01'})
        self.assertEqual([1, 2, 3], list_assets.call_args.args[0])
        self.assertEqual('dc01', list_assets.call_args.kwargs['search'])
        self.assertEqual(1, list_assets.call_args.kwargs['page'])
        self.assertEqual(50, list_assets.call_args.kwargs['per_page'])

    def test_iocs_list_covers_the_readable_cases(self):
        with patch(f'{_SCOPE}.war_room_scope_list_iocs', return_value={}) as list_iocs:
            scope_tools.iris_war_room_scope_iocs_list({'war_room_id': 3, 'page': 2})
        self.assertEqual([1, 2, 3], list_iocs.call_args.args[0])
        self.assertEqual(2, list_iocs.call_args.kwargs['page'])

    def test_registry_list_is_empty_without_asset_manager_read(self):
        with patch(f'{_SCOPE}.ac_current_user_has_permission', return_value=False), \
                patch(f'{_SCOPE}.war_room_managed_assets_list') as list_registry:
            result = scope_tools.iris_war_room_scope_registry_assets_list({'war_room_id': 3})
        self.assertFalse(result['allowed'])
        list_registry.assert_not_called()


class TestScopeWrites(_ScopeTest):

    def test_create_asset_only_targets_full_access_cases(self):
        asset = {'asset_name': 'dc01', 'asset_type_id': 1}
        with patch(f'{_SCOPE}.war_room_scope_create_asset', return_value={}) as create:
            scope_tools.iris_war_room_scope_assets_create({'war_room_id': 3, 'asset': asset, 'case_ids': [1, 2]})
        self.assertEqual((3, asset, [1, 2], [2]), (create.call_args.args[0], *create.call_args.args[2:]))

    def test_push_gets_readable_sources_and_writable_targets(self):
        with patch(f'{_SCOPE}.war_room_scope_push_iocs', return_value={}) as push:
            scope_tools.iris_war_room_scope_iocs_push({'war_room_id': 3, 'ioc_ids': [9], 'case_ids': [2]})
        self.assertEqual(([1, 2, 3], [2]), push.call_args.args[4:])

    def test_flag_defaults_to_set(self):
        with patch(f'{_SCOPE}.war_room_scope_bulk_flag', return_value={}) as flag:
            scope_tools.iris_war_room_scope_assets_flag({'war_room_id': 3, 'asset_ids': [4], 'flag_id': 6})
        self.assertEqual((3, 5, [4], 6, 'set'), flag.call_args.args[:5])

    def test_business_refusal_becomes_an_invalid_params_error(self):
        from app.models.errors import BusinessProcessingError
        with patch(f'{_SCOPE}.war_room_scope_bulk_status', side_effect=BusinessProcessingError('nope')):
            with self.assertRaises(MCPError):
                scope_tools.iris_war_room_scope_assets_status({'war_room_id': 3, 'asset_ids': [4]})

    def test_registry_attach_needs_asset_manager_read(self):
        with patch(f'{_SCOPE}.ac_current_user_has_permission', return_value=False), \
                patch(f'{_SCOPE}.war_room_managed_assets_attach') as attach:
            with self.assertRaises(MCPError):
                scope_tools.iris_war_room_scope_registry_assets_attach(
                    {'war_room_id': 3, 'managed_asset_ids': [1]})
        attach.assert_not_called()

    def test_staging_push_only_targets_full_access_cases(self):
        with patch(f'{_SCOPE}.war_room_scope_staged_push', return_value={}) as push:
            scope_tools.iris_war_room_scope_staging_push({'war_room_id': 3, 'staged_id': 12})
        self.assertEqual((12, None, [2]), push.call_args.args[2:])


class TestScopeRegistration(TestCase):

    _TOOLS = [name for name in TOOL_REGISTRY if name.startswith('iris_war_room_scope_')]

    def test_every_scope_tool_is_bound_to_the_war_room(self):
        self.assertEqual(13, len(self._TOOLS))
        for name in self._TOOLS:
            self.assertTrue(TOOL_REGISTRY[name].war_room_scoped, name)

    def test_only_the_lists_are_read_only(self):
        for name in self._TOOLS:
            self.assertEqual(name.endswith('_list'), name in classification.READ_ONLY_TOOLS, name)
