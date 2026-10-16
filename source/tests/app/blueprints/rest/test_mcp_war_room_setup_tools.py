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

"""The war room tools a workflow sets a room up with: create a room,
attach a case (full access on the case needed) and add a member (an
active user). The business layer is patched. No DB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp import classification
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools import war_rooms as war_rooms_tools
from app.iris_engine.ai_workflows.tools import AGENT_EXCLUDED_TOOLS
from app.models.authorization import Permissions
from app.models.authorization import WarRoomAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_WR = 'app.blueprints.rest.v2.mcp.tools.war_rooms'
_BIZ = f'{_WR}.war_rooms_biz'
_USER_ID = 5


def _user():
    user = MagicMock()
    user._get_current_object.return_value = SimpleNamespace(id=_USER_ID)
    return user


class _ToolTestCase(TestCase):

    def setUp(self):
        self.emit = MagicMock()
        patch(f'{_WR}.iris_current_user', _user()).start()
        patch(f'{_WR}.war_room_chat_biz.emit_system_event', self.emit).start()
        self.addCleanup(patch.stopall)

    def assertRefused(self, tool, args, code):
        with self.assertRaises(MCPError) as raised:
            tool(args)
        self.assertEqual(code, raised.exception.code)
        return raised.exception.message


class TestsRegistration(TestCase):

    def test_tools_should_be_classified_writes(self):
        for name in ('iris_war_rooms_create', 'iris_war_room_cases_attach', 'iris_war_room_members_add'):
            self.assertIn(name, classification.WRITE_TOOLS)

    def test_creation_should_need_the_create_permission(self):
        spec = TOOL_REGISTRY['iris_war_rooms_create']
        self.assertEqual((Permissions.war_rooms_create, Permissions.server_administrator), spec.permissions)
        self.assertFalse(spec.war_room_scoped)

    def test_attach_and_members_should_be_scoped_to_a_war_room(self):
        for name in ('iris_war_room_cases_attach', 'iris_war_room_members_add'):
            self.assertTrue(TOOL_REGISTRY[name].war_room_scoped)
            self.assertEqual((Permissions.war_rooms_write, Permissions.server_administrator),
                             TOOL_REGISTRY[name].permissions)

    def test_granting_access_should_be_opt_in_and_kept_from_agents(self):
        self.assertFalse(TOOL_REGISTRY['iris_war_room_members_add'].mvp)
        self.assertIn('iris_war_room_members_add', AGENT_EXCLUDED_TOOLS)


class TestsWarRoomsCreate(_ToolTestCase):

    def test_room_should_be_created_by_the_current_user(self):
        room = SimpleNamespace(war_room_id=12, name='Citrix NetScaler auth bypass', description='d', state='open',
                               severity_id=4, created_at=None, archived_at=None)
        with patch(f'{_BIZ}.war_room_create', return_value=room) as create:
            result = war_rooms_tools.iris_war_rooms_create({'name': room.name, 'description': 'd', 'severity_id': 4})
        create.assert_called_once_with(name=room.name, description='d', state=None, severity_id=4,
                                       created_by_id=_USER_ID)
        self.assertEqual((12, room.name, 4), (result['war_room_id'], result['name'], result['severity_id']))

    def test_invalid_room_should_be_refused(self):
        with patch(f'{_BIZ}.war_room_create', side_effect=BusinessProcessingError('Unknown severity_id')):
            message = self.assertRefused(war_rooms_tools.iris_war_rooms_create, {'name': 'x', 'severity_id': 99},
                                         protocol.INVALID_PARAMS)
        self.assertIn('severity_id', message)


class TestsWarRoomCasesAttach(_ToolTestCase):

    def setUp(self):
        super().setUp()
        self.case_access = patch(f'{_WR}.ac_fast_check_current_user_has_case_access', return_value=True).start()
        self.attach = patch(f'{_BIZ}.war_room_attach_case', return_value=SimpleNamespace(
            war_room_id=12, case_id=42, attached_at=None, note='hunt')).start()

    def test_case_should_be_attached_and_the_room_told(self):
        result = war_rooms_tools.iris_war_room_cases_attach({'war_room_id': 12, 'case_id': 42, 'note': 'hunt'})
        self.assertEqual({'war_room_id': 12, 'case_id': 42, 'attached_at': None, 'note': 'hunt'}, result)
        self.attach.assert_called_once_with(12, 42, attached_by_id=_USER_ID, note='hunt')
        self.case_access.assert_called_once()
        args, kwargs = self.emit.call_args
        self.assertEqual((12, 'case_attached', 'Attached case #42 — hunt'), args)
        self.assertEqual(('case', 42, 42, 'case.attached'),
                         (kwargs['ref_type'], kwargs['ref_id'], kwargs['ref_case_id'], kwargs['activity_type']))

    def test_case_without_full_access_should_be_refused(self):
        self.case_access.return_value = None
        self.assertRefused(war_rooms_tools.iris_war_room_cases_attach, {'war_room_id': 12, 'case_id': 42},
                           protocol.IRIS_ACCESS_DENIED)
        self.attach.assert_not_called()
        self.emit.assert_not_called()

    def test_missing_case_should_be_refused(self):
        self.attach.side_effect = BusinessProcessingError('Case not found')
        self.assertRefused(war_rooms_tools.iris_war_room_cases_attach, {'war_room_id': 12, 'case_id': 42},
                           protocol.INVALID_PARAMS)
        self.emit.assert_not_called()


class TestsWarRoomMembersAdd(_ToolTestCase):

    def setUp(self):
        super().setUp()
        self.active = patch(f'{_WR}.users_get_active', return_value=SimpleNamespace(id=8)).start()
        self.add = patch(f'{_BIZ}.war_room_add_member').start()
        patch(f'{_BIZ}.war_room_members_list', return_value=[
            SimpleNamespace(user_id=8, login='jdoe', name='J. Doe', role='observer')]).start()

    def test_member_should_be_added_with_the_access_asked(self):
        result = war_rooms_tools.iris_war_room_members_add(
            {'war_room_id': 12, 'user_id': 8, 'role': 'observer', 'access': 'read_only'})
        self.add.assert_called_once_with(12, 8, role='observer', added_by_id=_USER_ID,
                                         access_level=WarRoomAccessLevel.read_only.value)
        self.assertEqual(('jdoe', 'observer', 'read_only'), (result['user_login'], result['role'], result['access']))
        # The stream shows it from the war-room activity
        self.emit.assert_not_called()

    def test_access_should_default_to_full_access(self):
        war_rooms_tools.iris_war_room_members_add({'war_room_id': 12, 'user_id': 8})
        self.assertEqual(WarRoomAccessLevel.full_access.value, self.add.call_args.kwargs['access_level'])

    def test_inactive_or_unknown_user_should_be_refused(self):
        self.active.side_effect = ObjectNotFoundError()
        self.assertRefused(war_rooms_tools.iris_war_room_members_add, {'war_room_id': 12, 'user_id': 8},
                           protocol.INVALID_PARAMS)
        self.add.assert_not_called()
        self.emit.assert_not_called()

    def test_invalid_role_should_be_refused(self):
        self.add.side_effect = BusinessProcessingError('Role must be one of lead, observer, responder')
        self.assertRefused(war_rooms_tools.iris_war_room_members_add,
                           {'war_room_id': 12, 'user_id': 8, 'role': 'boss'}, protocol.INVALID_PARAMS)
        self.emit.assert_not_called()
