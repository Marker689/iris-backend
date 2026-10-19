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

"""Integration tests for `/api/v2/manage/tutorials` (admin-authored tutorials)."""

from unittest import TestCase

from iris import Iris

_TUTORIALS = '/api/v2/manage/tutorials'
_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789

_STEP = {
    'title': 'Open a case',
    'body': 'Click **Open a case**.',
    'anchor': 'case-new',
    'goTo': '/cases',
    'waitFor': {'kind': 'api', 'method': 'POST', 'path': '/cases', 'capture': {'caseId': 'case_id'}},
}


class TestsRestTutorials(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        for tutorial in self._subject.get(_TUTORIALS).json():
            self._subject.delete(f'{_TUTORIALS}/{tutorial["id"]}')
        self._subject.clear_database()

    def _create(self, **kwargs):
        body = {'title': 'Our triage routine', 'steps': [_STEP]}
        body.update(kwargs)
        return self._subject.create(_TUTORIALS, body)

    def test_create_tutorial_should_return_201(self):
        response = self._create()
        self.assertEqual(201, response.status_code)

    def test_create_tutorial_should_return_steps_in_camel_case(self):
        response = self._create().json()
        self.assertEqual([_STEP], response['steps'])

    def test_create_tutorial_should_default_to_a_draft(self):
        response = self._create().json()
        self.assertFalse(response['published'])

    def test_create_tutorial_should_return_400_when_go_to_leaves_the_app(self):
        step = dict(_STEP, goTo='https://evil.example')
        response = self._create(steps=[step])
        self.assertEqual(400, response.status_code)

    def test_create_published_tutorial_should_return_400_without_steps(self):
        response = self._create(steps=[], published=True)
        self.assertEqual(400, response.status_code)

    def test_published_should_only_list_published_tutorials(self):
        self._create(title='draft')
        self._create(title='live', published=True)
        response = self._subject.get(f'{_TUTORIALS}/published').json()
        self.assertEqual(['live'], [tutorial['title'] for tutorial in response])

    def test_published_should_be_readable_by_a_standard_user(self):
        self._create(title='live', published=True)
        user = self._subject.create_dummy_user()
        response = user.get(f'{_TUTORIALS}/published')
        self.assertEqual(200, response.status_code)

    def test_list_should_return_403_for_a_standard_user(self):
        user = self._subject.create_dummy_user()
        response = user.get(_TUTORIALS)
        self.assertEqual(403, response.status_code)

    def test_create_should_return_403_for_a_standard_user(self):
        user = self._subject.create_dummy_user()
        response = user.create(_TUTORIALS, {'title': 'x', 'steps': [_STEP]})
        self.assertEqual(403, response.status_code)

    def test_update_tutorial_should_publish_it(self):
        identifier = self._create().json()['id']
        response = self._subject.update(f'{_TUTORIALS}/{identifier}', {'published': True}).json()
        self.assertTrue(response['published'])

    def test_update_tutorial_should_return_400_when_publishing_an_empty_draft(self):
        identifier = self._create(steps=[]).json()['id']
        response = self._subject.update(f'{_TUTORIALS}/{identifier}', {'published': True})
        self.assertEqual(400, response.status_code)

    def test_update_tutorial_should_replace_the_steps(self):
        identifier = self._create().json()['id']
        step = {'title': 'Done', 'body': 'That is all.'}
        response = self._subject.update(f'{_TUTORIALS}/{identifier}', {'steps': [step]}).json()
        self.assertEqual([step], response['steps'])

    def test_get_tutorial_should_return_404_when_it_does_not_exist(self):
        response = self._subject.get(f'{_TUTORIALS}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_tutorial_should_return_204(self):
        identifier = self._create().json()['id']
        response = self._subject.delete(f'{_TUTORIALS}/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_delete_tutorial_should_remove_it(self):
        identifier = self._create().json()['id']
        self._subject.delete(f'{_TUTORIALS}/{identifier}')
        response = self._subject.get(f'{_TUTORIALS}/{identifier}')
        self.assertEqual(404, response.status_code)
