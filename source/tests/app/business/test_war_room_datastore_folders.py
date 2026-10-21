#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the folder helpers of war_room_datastore.py.

No DB, no Flask context needed: the helpers take plain rows.
"""

from types import SimpleNamespace
from unittest import TestCase

from app.business.war_room_datastore import _build_tree
from app.business.war_room_datastore import _folder_ids_under
from app.business.war_room_datastore import _sanitize_folder_name
from app.models.errors import BusinessProcessingError


def _folder(path_id, name, parent_id, is_root=False):
    return SimpleNamespace(path_id=path_id, path_name=name, path_parent_id=parent_id, path_is_root=is_root)


def _file(file_id, filename, parent_id):
    return SimpleNamespace(file_id=file_id, filename=filename, file_parent_id=parent_id)


def _serialize(row):
    return {'file_id': row.file_id, 'filename': row.filename}


_ROOT = _folder(1, 'root', None, is_root=True)


class TestWarRoomDatastoreFolderIdsUnder(TestCase):

    def test_should_include_the_folder_and_all_its_descendants(self):
        folders = [_ROOT, _folder(2, 'a', 1), _folder(3, 'b', 2), _folder(4, 'c', 3), _folder(5, 'd', 1)]
        self.assertEqual({2, 3, 4}, _folder_ids_under(folders, 2))

    def test_should_return_only_the_folder_when_it_has_no_children(self):
        folders = [_ROOT, _folder(2, 'a', 1)]
        self.assertEqual({2}, _folder_ids_under(folders, 2))

    def test_should_stop_on_a_loop(self):
        folders = [_folder(2, 'a', 3), _folder(3, 'b', 2)]
        self.assertEqual({2, 3}, _folder_ids_under(folders, 2))


class TestWarRoomDatastoreBuildTree(TestCase):

    def test_should_have_the_root_as_only_top_level_key(self):
        tree = _build_tree(_ROOT, [], [], _serialize)
        self.assertEqual(['d-1'], list(tree))
        self.assertTrue(tree['d-1']['is_root'])
        self.assertEqual('directory', tree['d-1']['type'])

    def test_should_nest_folders_and_files(self):
        tree = _build_tree(_ROOT, [_folder(3, 'sub', 2), _folder(2, 'top', 1)], [_file(7, 'x.txt', 3)], _serialize)
        sub = tree['d-1']['children']['d-2']['children']['d-3']
        self.assertEqual('sub', sub['name'])
        self.assertFalse(sub['is_root'])
        self.assertEqual({'file_id': 7, 'filename': 'x.txt', 'type': 'file'}, sub['children']['f-7'])

    def test_should_put_files_without_folder_in_the_root(self):
        tree = _build_tree(_ROOT, [], [_file(7, 'x.txt', None)], _serialize)
        self.assertIn('f-7', tree['d-1']['children'])

    def test_should_put_orphans_in_the_root(self):
        tree = _build_tree(_ROOT, [_folder(2, 'lost', 99)], [_file(7, 'x.txt', 98)], _serialize)
        self.assertIn('d-2', tree['d-1']['children'])
        self.assertIn('f-7', tree['d-1']['children'])


class TestWarRoomDatastoreSanitizeFolderName(TestCase):

    def test_should_strip_the_name(self):
        self.assertEqual('Evidence', _sanitize_folder_name('  Evidence '))

    def test_should_reject_a_blank_name(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_folder_name('   ')

    def test_should_reject_a_non_string(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_folder_name(3)
