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


"""Integration tests for the war-room and AI-workflow categories."""

from unittest import TestCase
from uuid import uuid4

from iris import Iris

_WAR_ROOMS = '/api/v2/war-rooms'
_AI_WORKFLOWS = '/api/v2/ai-workflows'
_WAR_ROOMS_READ = 0x8000
_WAR_ROOMS_WRITE = 0x10000
_AI_WORKFLOWS_READ = 0x80000000
_AI_WORKFLOWS_WRITE = 0x100000000


def _workflow_body():
    return {
        'name': f'workflow {uuid4()}',
        'is_active': False,
        'trigger_type': 'manual',
        'trigger_config': {'entity_types': ['alert']},
        'graph': {
            'nodes': [
                {'id': 'n1', 'type': 'trigger', 'label': 'Run', 'position': {'x': 0, 'y': 0}, 'config': {}},
                {'id': 'n2', 'type': 'stop', 'label': 'Done', 'position': {'x': 0, 'y': 100},
                 'config': {'status': 'succeeded', 'reason': 'done'}},
            ],
            'edges': [{'id': 'e1', 'source': 'n1', 'target': 'n2', 'source_port': 'out'}],
        },
    }


class TestsRestWarRoomCategories(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _category(self, name='Ransomware'):
        return self._subject.create(f'{_WAR_ROOMS}/categories', {'name': name}).json()['id']

    def _room(self, **extra):
        return self._subject.create(_WAR_ROOMS, {'name': 'Room', **extra}).json()['war_room_id']

    def test_create_category_should_return_201(self):
        response = self._subject.create(f'{_WAR_ROOMS}/categories', {'name': 'Ransomware'})
        self.assertEqual(201, response.status_code)
        self.assertEqual('Ransomware', response.json()['name'])

    def test_create_duplicate_category_whatever_the_case_should_return_400(self):
        self._category('Ransomware')
        response = self._subject.create(f'{_WAR_ROOMS}/categories', {'name': 'RANSOMWARE'})
        self.assertEqual(400, response.status_code)

    def test_list_categories_should_return_them_sorted_by_name(self):
        self._category('b')
        self._category('A')
        names = [c['name'] for c in self._subject.get(f'{_WAR_ROOMS}/categories').json()]
        self.assertEqual(['A', 'b'], names)

    def test_rename_category_should_return_200(self):
        category_id = self._category()
        response = self._subject.patch(f'{_WAR_ROOMS}/categories/{category_id}', {'name': 'Phishing'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('Phishing', response.json()['name'])

    def test_move_war_room_should_set_its_category(self):
        category_id = self._category()
        room_id = self._room()
        response = self._subject.update(f'{_WAR_ROOMS}/{room_id}/category', {'category_id': category_id})
        self.assertEqual(200, response.status_code)
        self.assertEqual(category_id, response.json()['category_id'])
        self.assertEqual(category_id, self._subject.get(f'{_WAR_ROOMS}/{room_id}').json()['category_id'])

    def test_move_war_room_to_null_should_clear_its_category(self):
        room_id = self._room(category_id=self._category())
        response = self._subject.update(f'{_WAR_ROOMS}/{room_id}/category', {'category_id': None})
        self.assertIsNone(response.json()['category_id'])

    def test_move_without_category_id_should_return_400(self):
        room_id = self._room()
        response = self._subject.update(f'{_WAR_ROOMS}/{room_id}/category', {})
        self.assertEqual(400, response.status_code)

    def test_move_to_unknown_category_should_return_400(self):
        room_id = self._room()
        response = self._subject.update(f'{_WAR_ROOMS}/{room_id}/category', {'category_id': 123456789})
        self.assertEqual(400, response.status_code)

    def test_move_to_an_ai_workflow_category_should_return_400(self):
        category_id = self._subject.create(f'{_AI_WORKFLOWS}/categories', {'name': 'Triage'}).json()['id']
        room_id = self._room()
        response = self._subject.update(f'{_WAR_ROOMS}/{room_id}/category', {'category_id': category_id})
        self.assertEqual(400, response.status_code)

    def test_delete_category_should_uncategorise_its_war_rooms(self):
        category_id = self._category()
        room_id = self._room(category_id=category_id)
        response = self._subject.delete(f'{_WAR_ROOMS}/categories/{category_id}')
        self.assertEqual(204, response.status_code)
        self.assertIsNone(self._subject.get(f'{_WAR_ROOMS}/{room_id}').json()['category_id'])

    def test_user_without_war_room_permission_should_not_list_categories(self):
        user = self._subject.create_dummy_user()
        response = user.get(f'{_WAR_ROOMS}/categories')
        self.assertEqual(403, response.status_code)

    def test_read_only_user_should_list_but_not_manage_categories(self):
        category_id = self._category()
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ)
        self.assertEqual(200, user.get(f'{_WAR_ROOMS}/categories').status_code)
        self.assertEqual(403, user.create(f'{_WAR_ROOMS}/categories', {'name': 'X'}).status_code)
        self.assertEqual(403, user.patch(f'{_WAR_ROOMS}/categories/{category_id}', {'name': 'X'}).status_code)
        self.assertEqual(403, user.delete(f'{_WAR_ROOMS}/categories/{category_id}').status_code)

    def test_writer_without_access_to_the_room_should_not_move_it(self):
        category_id = self._category()
        room_id = self._room()
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ | _WAR_ROOMS_WRITE)
        response = user.update(f'{_WAR_ROOMS}/{room_id}/category', {'category_id': category_id})
        self.assertEqual(403, response.status_code)
        self.assertIsNone(self._subject.get(f'{_WAR_ROOMS}/{room_id}').json()['category_id'])

    def test_member_with_write_should_move_the_room(self):
        category_id = self._category()
        room_id = self._room()
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ | _WAR_ROOMS_WRITE)
        self._subject.create(f'{_WAR_ROOMS}/{room_id}/members', {'user_id': user.get_identifier()})
        response = user.update(f'{_WAR_ROOMS}/{room_id}/category', {'category_id': category_id})
        self.assertEqual(200, response.status_code)


class TestsRestAiWorkflowCategories(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        # The owner FK is RESTRICT: workflows go before the users
        workflows = self._subject.get(_AI_WORKFLOWS).json()
        if isinstance(workflows, list):
            for workflow in workflows:
                self._subject.delete(f"{_AI_WORKFLOWS}/{workflow['id']}")
        self._subject.clear_database()

    def _category(self, name='Triage'):
        return self._subject.create(f'{_AI_WORKFLOWS}/categories', {'name': name}).json()['id']

    def _workflow(self, subject=None):
        return (subject or self._subject).create(_AI_WORKFLOWS, _workflow_body()).json()

    def test_move_workflow_should_set_its_category_without_a_new_version(self):
        category_id = self._category()
        workflow = self._workflow()
        response = self._subject.update(f"{_AI_WORKFLOWS}/{workflow['id']}/category", {'category_id': category_id})
        self.assertEqual(200, response.status_code)
        self.assertEqual(category_id, response.json()['category_id'])
        self.assertEqual(workflow['version'], response.json()['version'])

    def test_list_workflows_should_carry_the_category(self):
        category_id = self._category()
        workflow = self._workflow()
        self._subject.update(f"{_AI_WORKFLOWS}/{workflow['id']}/category", {'category_id': category_id})
        listed = {w['id']: w for w in self._subject.get(_AI_WORKFLOWS).json()}
        self.assertEqual(category_id, listed[workflow['id']]['category_id'])

    def test_move_to_a_war_room_category_should_return_400(self):
        category_id = self._subject.create(f'{_WAR_ROOMS}/categories', {'name': 'Ransomware'}).json()['id']
        workflow = self._workflow()
        response = self._subject.update(f"{_AI_WORKFLOWS}/{workflow['id']}/category", {'category_id': category_id})
        self.assertEqual(400, response.status_code)

    def test_delete_category_should_uncategorise_its_workflows(self):
        category_id = self._category()
        workflow = self._workflow()
        self._subject.update(f"{_AI_WORKFLOWS}/{workflow['id']}/category", {'category_id': category_id})
        self.assertEqual(204, self._subject.delete(f'{_AI_WORKFLOWS}/categories/{category_id}').status_code)
        self.assertIsNone(self._subject.get(f"{_AI_WORKFLOWS}/{workflow['id']}").json()['category_id'])

    def test_reader_should_list_but_not_manage_categories(self):
        category_id = self._category()
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ)
        self.assertEqual(200, user.get(f'{_AI_WORKFLOWS}/categories').status_code)
        self.assertEqual(403, user.create(f'{_AI_WORKFLOWS}/categories', {'name': 'X'}).status_code)
        self.assertEqual(403, user.delete(f'{_AI_WORKFLOWS}/categories/{category_id}').status_code)

    def test_writer_should_not_move_someone_else_s_workflow(self):
        category_id = self._category()
        workflow = self._workflow()
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        response = user.update(f"{_AI_WORKFLOWS}/{workflow['id']}/category", {'category_id': category_id})
        self.assertEqual(404, response.status_code)
        self.assertIsNone(self._subject.get(f"{_AI_WORKFLOWS}/{workflow['id']}").json()['category_id'])
