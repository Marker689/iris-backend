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


"""Unit tests for the war-room and AI-workflow categories (no database)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import ai_workflows
from app.business import categories
from app.business import war_rooms
from app.models.categories import CATEGORY_KIND_AI_WORKFLOW
from app.models.categories import CATEGORY_KIND_WAR_ROOM
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_BIZ = 'app.business.categories'


class _Store(TestCase):
    """In-memory stand-in for `categories_db`."""

    def setUp(self):
        self.rows = {
            1: SimpleNamespace(id=1, kind=CATEGORY_KIND_WAR_ROOM, name='Ransomware', created_at=None,
                               created_by_id=3),
            2: SimpleNamespace(id=2, kind=CATEGORY_KIND_AI_WORKFLOW, name='Triage', created_at=None,
                               created_by_id=3),
        }
        self.saved = []
        self.save_result = True

        def get(kind, category_id):
            row = self.rows.get(category_id)
            return row if row is not None and row.kind == kind else None

        def taken(kind, name, exclude_id=None):
            return any(r.kind == kind and r.name.lower() == name.lower() and r.id != exclude_id
                       for r in self.rows.values())

        def save(category):
            self.saved.append(category)
            return self.save_result

        self._patchers = [
            patch(f'{_BIZ}.categories_db_get', side_effect=get),
            patch(f'{_BIZ}.categories_db_name_taken', side_effect=taken),
            patch(f'{_BIZ}.categories_db_save', side_effect=save),
            patch(f'{_BIZ}.categories_db_delete'),
            patch(f'{_BIZ}.track_activity'),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.delete = self.mocks[3]

    def tearDown(self):
        for p in self._patchers:
            p.stop()


class TestCategories(_Store):

    def test_create_normalises_the_name(self):
        category = categories.categories_create(CATEGORY_KIND_WAR_ROOM, '  Supply   chain ', 3)
        self.assertEqual('Supply chain', category.name)
        self.assertEqual(CATEGORY_KIND_WAR_ROOM, category.kind)
        self.assertEqual(3, category.created_by_id)
        self.assertEqual([category], self.saved)

    def test_create_refuses_a_taken_name_whatever_the_case(self):
        with self.assertRaises(BusinessProcessingError):
            categories.categories_create(CATEGORY_KIND_WAR_ROOM, 'RANSOMWARE', 3)

    def test_same_name_allowed_in_the_other_kind(self):
        category = categories.categories_create(CATEGORY_KIND_AI_WORKFLOW, 'Ransomware', 3)
        self.assertEqual('Ransomware', category.name)

    def test_create_refuses_empty_long_or_non_string_names(self):
        for name in (None, '', '   ', 12, 'x' * 129):
            with self.subTest(name=name), self.assertRaises(BusinessProcessingError):
                categories.categories_create(CATEGORY_KIND_WAR_ROOM, name, 3)

    def test_create_refuses_an_unknown_kind(self):
        with self.assertRaises(BusinessProcessingError):
            categories.categories_create('case', 'X', 3)

    def test_concurrent_duplicate_is_a_400(self):
        self.save_result = False
        with self.assertRaises(BusinessProcessingError):
            categories.categories_create(CATEGORY_KIND_WAR_ROOM, 'Phishing', 3)

    def test_rename_keeps_its_own_name_available(self):
        category = categories.categories_rename(CATEGORY_KIND_WAR_ROOM, 1, 'ransomware')
        self.assertEqual('ransomware', category.name)

    def test_rename_or_delete_across_kinds_is_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            categories.categories_rename(CATEGORY_KIND_WAR_ROOM, 2, 'X')
        with self.assertRaises(ObjectNotFoundError):
            categories.categories_delete(CATEGORY_KIND_AI_WORKFLOW, 1)
        self.delete.assert_not_called()

    def test_delete(self):
        categories.categories_delete(CATEGORY_KIND_WAR_ROOM, 1)
        self.delete.assert_called_once_with(self.rows[1])

    def test_resolve_id(self):
        self.assertIsNone(categories.categories_resolve_id(CATEGORY_KIND_WAR_ROOM, None))
        self.assertEqual(1, categories.categories_resolve_id(CATEGORY_KIND_WAR_ROOM, 1))
        for value in (2, 99, True, '1', 1.0):
            with self.subTest(value=value), self.assertRaises(BusinessProcessingError):
                categories.categories_resolve_id(CATEGORY_KIND_WAR_ROOM, value)


class TestWarRoomSetCategory(_Store):

    def setUp(self):
        super().setUp()
        self.room = SimpleNamespace(war_room_id=5, name='Room', category_id=None, category=None)
        self._room_patchers = [
            patch('app.business.war_rooms.war_room_get', return_value=self.room),
            patch('app.business.war_rooms.db'),
            patch('app.business.war_rooms.track_activity'),
        ]
        mocks = [p.start() for p in self._room_patchers]
        self.db = mocks[1]

    def tearDown(self):
        for p in self._room_patchers:
            p.stop()
        super().tearDown()

    def test_move_and_clear(self):
        war_rooms.war_room_set_category(5, 1)
        self.assertEqual(1, self.room.category_id)
        war_rooms.war_room_set_category(5, None)
        self.assertIsNone(self.room.category_id)
        self.assertEqual(2, self.db.session.commit.call_count)

    def test_workflow_category_refused(self):
        with self.assertRaises(BusinessProcessingError):
            war_rooms.war_room_set_category(5, 2)
        self.assertIsNone(self.room.category_id)
        self.db.session.commit.assert_not_called()


class TestAiWorkflowSetCategory(_Store):

    def setUp(self):
        super().setUp()
        self.workflow = SimpleNamespace(id=9, name='Flow', owner_id=3, category_id=None, category=None)
        self._wf_patchers = [
            patch('app.business.ai_workflows.ai_workflows_db_get',
                  side_effect=lambda i: self.workflow if i == 9 else None),
            patch('app.business.ai_workflows.ai_workflows_db_commit'),
            patch('app.business.ai_workflows.ai_workflows_get', return_value={'id': 9}),
            patch('app.business.ai_workflows.track_activity'),
        ]
        mocks = [p.start() for p in self._wf_patchers]
        self.commit = mocks[1]

    def tearDown(self):
        for p in self._wf_patchers:
            p.stop()
        super().tearDown()

    def test_owner_moves(self):
        ai_workflows.ai_workflows_set_category(9, 2, 3, False)
        self.assertEqual(2, self.workflow.category_id)
        self.commit.assert_called_once()

    def test_someone_else_s_workflow_is_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows.ai_workflows_set_category(9, 2, 4, False)
        self.assertIsNone(self.workflow.category_id)

    def test_admin_moves_any_workflow(self):
        ai_workflows.ai_workflows_set_category(9, 2, 4, True)
        self.assertEqual(2, self.workflow.category_id)

    def test_war_room_category_refused(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows.ai_workflows_set_category(9, 1, 3, False)
        self.commit.assert_not_called()

    def test_unchanged_does_not_commit(self):
        self.workflow.category_id = 2
        ai_workflows.ai_workflows_set_category(9, 2, 3, False)
        self.commit.assert_not_called()


class TestSerializers(TestCase):

    def test_war_room_carries_its_category(self):
        from app.blueprints.rest.v2.war_rooms.serializers import serialize_war_room
        room = MagicMock(category_id=4, created_at=None, closed_at=None, archived_at=None, war_room_uuid=None)
        self.assertEqual(4, serialize_war_room(room)['category_id'])
