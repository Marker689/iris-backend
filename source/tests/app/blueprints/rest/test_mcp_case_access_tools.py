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

"""The case access MCP tools hold their callers to the rules of
`/api/v2/cases/<id>/access/*`: the tool bodies run with the guards real
and the business layer patched. No DB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.classification import is_read_only
from app.blueprints.rest.v2.mcp.classification import is_write
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools import case_access as tools
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import ObjectNotFoundError

_TOOLS = 'app.blueprints.rest.v2.mcp.tools.case_access'
_GUARDS = 'app.blueprints.rest.v2.cases_access_guards'

_CASE_IDENTIFIER = 11
_CUSTOMER_IDENTIFIER = 3
_CALLER_IDENTIFIER = 7
_TARGET_IDENTIFIER = 23
_GROUP_IDENTIFIER = 5

_FULL = CaseAccessLevel.full_access.value
_READ = CaseAccessLevel.read_only.value


class _ToolsTestCase(TestCase):

    def setUp(self):
        self.case = SimpleNamespace(case_id=_CASE_IDENTIFIER, client_id=_CUSTOMER_IDENTIFIER)
        self.permissions = {Permissions.case_access_manage}
        self.case_level = _FULL

        patch(f'{_GUARDS}.iris_current_user', SimpleNamespace(id=_CALLER_IDENTIFIER)).start()
        patch(f'{_GUARDS}.ac_current_user_has_permission',
              side_effect=lambda permission: permission in self.permissions).start()
        patch(f'{_GUARDS}.ac_fast_check_current_user_has_case_access',
              side_effect=lambda _identifier, levels: self.case_level
              if any(self.case_level & level.value for level in levels) else None).start()
        self.sees_customer = patch(f'{_GUARDS}.cases_access_user_sees_customer', return_value=True).start()
        self.get_case = patch(f'{_TOOLS}.cases_get_by_identifier', return_value=self.case).start()

        self.target = SimpleNamespace(id=_TARGET_IDENTIFIER)
        self.get_user = patch(f'{_TOOLS}.cases_access_get_user',
                              side_effect=lambda identifier: self.target if identifier == _TARGET_IDENTIFIER
                              else SimpleNamespace(id=identifier)).start()
        self.set_user = patch(f'{_TOOLS}.cases_access_set_user',
                              side_effect=lambda _case, _user, level: CaseAccessLevel(level)).start()
        self.group = SimpleNamespace(group_id=_GROUP_IDENTIFIER, group_members=[{'id': _TARGET_IDENTIFIER}])
        self.get_group = patch(f'{_TOOLS}.cases_access_get_group', return_value=self.group).start()
        self.set_group = patch(f'{_TOOLS}.cases_access_set_group',
                               side_effect=lambda _case, _group, level: CaseAccessLevel(level)).start()

        context = app.test_request_context('/')
        context.push()
        self.addCleanup(context.pop)
        self.addCleanup(patch.stopall)

    def assertRefused(self, call, code=protocol.INVALID_PARAMS, message=None):
        with self.assertRaises(MCPError) as raised:
            call()
        self.assertEqual(code, raised.exception.code)
        if message:
            self.assertIn(message, raised.exception.message)


class TestsCaseAccessToolsRegistration(TestCase):

    def test_list_should_be_a_read_and_the_setters_writes(self):
        self.assertTrue(is_read_only('iris_case_access_list'))
        self.assertTrue(is_write('iris_case_access_set_user'))
        self.assertTrue(is_write('iris_case_access_set_group'))

    def test_tools_should_be_case_scoped_and_opt_in(self):
        for name in ('iris_case_access_list', 'iris_case_access_set_user', 'iris_case_access_set_group'):
            spec = TOOL_REGISTRY[name]
            self.assertTrue(spec.case_scoped, name)
            self.assertFalse(spec.mvp, name)
            self.assertFalse(spec.admin_only, name)

    def test_setters_should_need_the_access_management_permission(self):
        for name in ('iris_case_access_set_user', 'iris_case_access_set_group'):
            self.assertEqual((Permissions.case_access_manage, Permissions.server_administrator),
                             TOOL_REGISTRY[name].permissions)


class TestsCaseAccessList(_ToolsTestCase):

    def setUp(self):
        super().setUp()
        patch(f'{_TOOLS}.get_users_list_restricted_from_case', return_value=[{
            'user_id': _TARGET_IDENTIFIER, 'user_uuid': 'u', 'user_name': 'Target', 'user_login': 'target',
            'user_active': True, 'user_email': 'target@example.org', 'user_access_level': _READ,
        }]).start()
        self.list_groups = patch(f'{_TOOLS}.cases_access_list_groups', return_value=[
            {'group_id': _GROUP_IDENTIFIER, 'group_name': 'SOC', 'group_description': 'd', 'access_level': None},
        ]).start()

    def test_manager_should_get_users_and_groups_with_level_names(self):
        result = tools.iris_case_access_list({'case_identifier': _CASE_IDENTIFIER})
        self.assertEqual({
            'case_id': _CASE_IDENTIFIER,
            'users': [{'user_id': _TARGET_IDENTIFIER, 'user_name': 'Target', 'user_login': 'target',
                       'user_active': True, 'access_level': 'read_only'}],
            'can_manage_access': True,
            'groups': [{'group_id': _GROUP_IDENTIFIER, 'group_name': 'SOC', 'access_level': None}],
        }, result)

    def test_reader_should_get_the_users_only(self):
        self.case_level = _READ
        result = tools.iris_case_access_list({'case_identifier': _CASE_IDENTIFIER})
        self.assertFalse(result['can_manage_access'])
        self.assertNotIn('groups', result)
        self.list_groups.assert_not_called()

    def test_unknown_case_should_be_refused(self):
        self.get_case.side_effect = ObjectNotFoundError()
        self.assertRefused(lambda: tools.iris_case_access_list({'case_identifier': _CASE_IDENTIFIER}))


class TestsCaseAccessSetUser(_ToolsTestCase):

    def _set(self, user_identifier=_TARGET_IDENTIFIER, level='read_only'):
        return tools.iris_case_access_set_user({'case_identifier': _CASE_IDENTIFIER, 'user_id': user_identifier,
                                                'access_level': level})

    def test_manager_should_set_the_level(self):
        self.assertEqual({'case_id': _CASE_IDENTIFIER, 'user_id': _TARGET_IDENTIFIER, 'access_level': 'read_only'},
                         self._set())
        self.set_user.assert_called_once_with(self.case, self.target, _READ)

    def test_reader_with_the_permission_should_be_denied(self):
        self.case_level = _READ
        self.assertRefused(self._set, protocol.IRIS_ACCESS_DENIED)
        self.set_user.assert_not_called()

    def test_nobody_should_change_their_own_access(self):
        for permissions in ({Permissions.case_access_manage}, {Permissions.server_administrator}):
            self.permissions = permissions
            self.assertRefused(lambda: self._set(_CALLER_IDENTIFIER), message='your own access')
        self.set_user.assert_not_called()

    def test_user_who_cannot_see_the_customer_should_be_refused(self):
        self.sees_customer.return_value = False
        self.assertRefused(self._set, message='customer')
        self.set_user.assert_not_called()

    def test_administrator_should_not_be_held_to_the_customer_guard(self):
        self.permissions = {Permissions.server_administrator}
        self.sees_customer.return_value = False
        self.assertEqual('full_access', self._set(level='full_access')['access_level'])

    def test_unknown_level_or_user_should_be_refused(self):
        for level in ('admin', None, _FULL):
            self.assertRefused(lambda: self._set(level=level), message='access_level')
        self.get_user.side_effect = ObjectNotFoundError()
        self.assertRefused(self._set, message='User not found')
        self.set_user.assert_not_called()


class TestsCaseAccessSetGroup(_ToolsTestCase):

    def _set(self, level='read_only'):
        return tools.iris_case_access_set_group({'case_identifier': _CASE_IDENTIFIER, 'group_id': _GROUP_IDENTIFIER,
                                                 'access_level': level})

    def test_manager_should_set_the_level(self):
        self.assertEqual({'case_id': _CASE_IDENTIFIER, 'group_id': _GROUP_IDENTIFIER, 'access_level': 'read_only'},
                         self._set())
        self.set_group.assert_called_once_with(self.case, self.group, _READ)

    def test_may_not_lower_a_group_the_caller_belongs_to(self):
        self.group.group_members.append({'id': _CALLER_IDENTIFIER})
        self.assertRefused(self._set, message='group you belong to')
        self.assertEqual('full_access', self._set('full_access')['access_level'])

    def test_group_with_a_member_who_cannot_see_the_customer_should_be_refused(self):
        self.sees_customer.return_value = False
        self.assertRefused(lambda: self._set('full_access'), message='customer')
        self.set_group.assert_not_called()

    def test_administrator_should_not_be_held_to_the_member_guards(self):
        self.permissions = {Permissions.server_administrator}
        self.case_level = CaseAccessLevel.deny_all.value
        self.sees_customer.return_value = False
        self.group.group_members.append({'id': _CALLER_IDENTIFIER})
        self.assertEqual('deny_all', self._set('deny_all')['access_level'])

    def test_unknown_group_should_be_refused(self):
        self.get_group.side_effect = ObjectNotFoundError()
        self.assertRefused(self._set, message='Group not found')
