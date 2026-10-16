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

"""Importing an IRIS group as a war-room team: a copy of the group's
name, description and active members, the ones not in the war room yet
joining it as responder."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import call
from unittest.mock import patch

from app.business import war_room_teams
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BIZ = 'app.business.war_room_teams'


class TestsWarRoomTeamImportGroup(TestCase):

    def setUp(self):
        self.group = SimpleNamespace(group_id=7, group_name='Analysts', group_description='Tier 2')
        self.team = SimpleNamespace(team_id=4, war_room_id=3, name='Analysts')
        patchers = {
            'get_group': patch(f'{_BIZ}.get_group', side_effect=lambda gid: self.group if gid == 7 else None),
            'members': patch(f'{_BIZ}.get_group_active_member_identifiers', return_value=[11, 12]),
            'create': patch(f'{_BIZ}.war_room_team_create', return_value=self.team),
            'add': patch(f'{_BIZ}.war_room_team_member_add',
                         side_effect=lambda _wr, _tid, user_id, **_kwargs: (None, user_id == 12)),
        }
        self.mocks = {}
        for key, patcher in patchers.items():
            self.mocks[key] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_team_copies_the_group(self):
        team, member_ids, auto_added = war_room_teams.war_room_team_import_group(3, 7, imported_by_id=1)
        self.assertIs(self.team, team)
        self.assertEqual([11, 12], member_ids)
        self.assertEqual([12], auto_added)
        self.mocks['create'].assert_called_once_with(3, 'Analysts', description='Tier 2', color=None,
                                                     created_by_id=1)
        self.assertEqual([call(3, 4, 11, added_by_id=1), call(3, 4, 12, added_by_id=1)],
                         self.mocks['add'].call_args_list)

    def test_the_team_can_be_named_differently(self):
        war_room_teams.war_room_team_import_group(3, 7, name='Blue', color='#aabbcc')
        self.mocks['create'].assert_called_once_with(3, 'Blue', description='Tier 2', color='#aabbcc',
                                                     created_by_id=None)

    def test_a_group_without_description_is_named_in_the_team_description(self):
        self.group.group_description = None
        war_room_teams.war_room_team_import_group(3, 7)
        self.assertEqual('Imported from the IRIS group "Analysts"',
                         self.mocks['create'].call_args.kwargs['description'])

    def test_a_long_group_name_is_cut_to_the_team_name_limit(self):
        self.group.group_name = 'g' * 120
        war_room_teams.war_room_team_import_group(3, 7)
        self.assertEqual('g' * 80, self.mocks['create'].call_args.args[1])

    def test_an_unknown_group_is_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            war_room_teams.war_room_team_import_group(3, 8)
        self.mocks['create'].assert_not_called()

    def test_the_group_id_must_be_an_integer(self):
        for group_id in (None, '7', True):
            with self.assertRaises(BusinessProcessingError):
                war_room_teams.war_room_team_import_group(3, group_id)
        self.mocks['create'].assert_not_called()

    def test_a_name_clash_creates_nothing(self):
        self.mocks['create'].side_effect = BusinessProcessingError('A team with that name already exists')
        with self.assertRaises(BusinessProcessingError):
            war_room_teams.war_room_team_import_group(3, 7)
        self.mocks['add'].assert_not_called()
