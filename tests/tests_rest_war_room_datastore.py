#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Integration tests for the folders of the war-room datastore.

Same endpoints and bodies as the case datastore (`tests_rest_datastore.py`).
"""

import io
from unittest import TestCase

from iris import Iris

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestWarRoomDatastore(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _room(self, name='Datastore room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

    def _tree(self, room_id):
        return self._subject.get(f'/api/v2/war-rooms/{room_id}/datastore/tree').json()

    def _root_id(self, room_id):
        return int(next(iter(self._tree(room_id))).split('-')[1])

    def _create_folder(self, room_id, name='Folder', parent=None):
        if parent is None:
            parent = self._root_id(room_id)
        body = {'parent_node': parent, 'folder_name': name}
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders', body)

    def _upload(self, room_id, folder_id=None, filename='hello.txt', content=b'hello world'):
        path = f'/api/v2/war-rooms/{room_id}/datastore'
        if folder_id is not None:
            path = f'{path}/folders/{folder_id}/files'
        files = {'file': (filename, io.BytesIO(content), 'application/octet-stream')}
        return self._subject.post_multipart_encoded_files(path, {}, files)

    # ------------------------------------------------------------------
    # tree
    # ------------------------------------------------------------------
    def test_get_tree_should_return_200(self):
        room_id = self._room()
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/datastore/tree')
        self.assertEqual(200, response.status_code)

    def test_get_tree_should_return_404_when_war_room_does_not_exist(self):
        response = self._subject.get(f'/api/v2/war-rooms/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/datastore/tree')
        self.assertEqual(404, response.status_code)

    def test_get_tree_should_return_403_when_user_has_no_access(self):
        room_id = self._room()
        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/war-rooms/{room_id}/datastore/tree')
        self.assertEqual(403, response.status_code)

    def test_get_tree_should_have_a_root_directory(self):
        room_id = self._room()
        tree = self._tree(room_id)
        self.assertEqual(1, len(tree))
        root = next(iter(tree.values()))
        self.assertTrue(root['is_root'])
        self.assertEqual('directory', root['type'])

    def test_get_tree_should_put_files_uploaded_without_folder_in_the_root(self):
        room_id = self._room()
        file_id = self._upload(room_id).json()['file_id']
        root = next(iter(self._tree(room_id).values()))
        self.assertIn(f'f-{file_id}', root['children'])

    # ------------------------------------------------------------------
    # folders
    # ------------------------------------------------------------------
    def test_add_folder_should_return_201(self):
        room_id = self._room()
        response = self._create_folder(room_id, 'Evidence')
        self.assertEqual(201, response.status_code)
        self.assertEqual('Evidence', response.json()['path_name'])

    def test_add_folder_should_nest_subfolders(self):
        room_id = self._room()
        parent_id = self._create_folder(room_id, 'Parent').json()['path_id']
        child_id = self._create_folder(room_id, 'Child', parent_id).json()['path_id']
        root = next(iter(self._tree(room_id).values()))
        self.assertIn(f'd-{child_id}', root['children'][f'd-{parent_id}']['children'])

    def test_add_folder_should_return_400_when_missing_fields(self):
        room_id = self._room()
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders', {})
        self.assertEqual(400, response.status_code)

    def test_add_folder_should_return_400_when_parent_belongs_to_another_war_room(self):
        room_id = self._room()
        other_root = self._root_id(self._room('Other room'))
        response = self._create_folder(room_id, 'Folder', other_root)
        self.assertEqual(400, response.status_code)

    def test_add_folder_should_return_403_when_user_has_no_access(self):
        room_id = self._room()
        root_id = self._root_id(room_id)
        user = self._subject.create_dummy_user()
        response = user.create(f'/api/v2/war-rooms/{room_id}/datastore/folders',
                               {'parent_node': root_id, 'folder_name': 'x'})
        self.assertEqual(403, response.status_code)

    def test_list_folders_should_return_the_root_and_the_folders(self):
        room_id = self._room()
        self._create_folder(room_id)
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/datastore/folders')
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(response.json()))

    def test_get_folder_should_return_404_when_missing(self):
        room_id = self._room()
        response = self._subject.get(
            f'/api/v2/war-rooms/{room_id}/datastore/folders/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_rename_folder_should_return_200(self):
        room_id = self._room()
        folder_id = self._create_folder(room_id, 'Old').json()['path_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders/{folder_id}/rename',
                                        {'folder_name': 'New'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('New', response.json()['path_name'])

    def test_move_folder_should_return_200(self):
        room_id = self._room()
        a = self._create_folder(room_id, 'A').json()['path_id']
        b = self._create_folder(room_id, 'B').json()['path_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders/{a}/move',
                                        {'destination_node': b})
        self.assertEqual(200, response.status_code)
        self.assertEqual(b, response.json()['path_parent_id'])

    def test_move_folder_should_return_400_when_same_destination(self):
        room_id = self._room()
        a = self._create_folder(room_id, 'A').json()['path_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders/{a}/move',
                                        {'destination_node': a})
        self.assertEqual(400, response.status_code)

    def test_move_folder_should_return_400_when_destination_is_a_subfolder(self):
        room_id = self._room()
        parent = self._create_folder(room_id, 'Parent').json()['path_id']
        child = self._create_folder(room_id, 'Child', parent).json()['path_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders/{parent}/move',
                                        {'destination_node': child})
        self.assertEqual(400, response.status_code)

    def test_move_folder_should_return_400_when_moving_the_root(self):
        room_id = self._room()
        root_id = self._root_id(room_id)
        folder_id = self._create_folder(room_id).json()['path_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/folders/{root_id}/move',
                                        {'destination_node': folder_id})
        self.assertEqual(400, response.status_code)

    def test_delete_folder_should_remove_its_files_and_subfolders(self):
        room_id = self._room()
        parent = self._create_folder(room_id, 'Parent').json()['path_id']
        child = self._create_folder(room_id, 'Child', parent).json()['path_id']
        file_id = self._upload(room_id, child).json()['file_id']
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/datastore/folders/{parent}')
        self.assertEqual(204, response.status_code)
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/datastore/{file_id}')
        self.assertEqual(404, response.status_code)
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/datastore/folders/{child}')
        self.assertEqual(404, response.status_code)

    def test_delete_root_folder_should_return_400(self):
        room_id = self._room()
        root_id = self._root_id(room_id)
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/datastore/folders/{root_id}')
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------
    def test_upload_to_folder_should_return_201(self):
        room_id = self._room()
        folder_id = self._create_folder(room_id).json()['path_id']
        response = self._upload(room_id, folder_id)
        self.assertEqual(201, response.status_code)
        self.assertEqual(folder_id, response.json()['file_parent_id'])

    def test_upload_to_folder_should_return_404_when_folder_missing(self):
        room_id = self._room()
        response = self._upload(room_id, _IDENTIFIER_FOR_NONEXISTENT_OBJECT)
        self.assertEqual(404, response.status_code)

    def test_move_file_should_return_200(self):
        room_id = self._room()
        folder_id = self._create_folder(room_id).json()['path_id']
        file_id = self._upload(room_id).json()['file_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/{file_id}/move',
                                        {'destination_node': folder_id})
        self.assertEqual(200, response.status_code)
        self.assertEqual(folder_id, response.json()['file_parent_id'])

    def test_move_file_should_return_400_when_destination_belongs_to_another_war_room(self):
        room_id = self._room()
        other_root = self._root_id(self._room('Other room'))
        file_id = self._upload(room_id).json()['file_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/datastore/{file_id}/move',
                                        {'destination_node': other_root})
        self.assertEqual(400, response.status_code)

    def test_update_file_should_rename_it(self):
        room_id = self._room()
        file_id = self._upload(room_id).json()['file_id']
        response = self._subject.post_multipart_encoded_files(
            f'/api/v2/war-rooms/{room_id}/datastore/{file_id}',
            {'filename': 'renamed.txt', 'description': 'Notes'},
            {})
        self.assertEqual(200, response.status_code)
        self.assertEqual('renamed.txt', response.json()['filename'])
        self.assertEqual('Notes', response.json()['description'])

    def test_update_file_should_replace_its_content(self):
        room_id = self._room()
        file_id = self._upload(room_id).json()['file_id']
        self._subject.post_multipart_encoded_files(
            f'/api/v2/war-rooms/{room_id}/datastore/{file_id}',
            {},
            {'file': ('hello.txt', io.BytesIO(b'new content'), 'application/octet-stream')})
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/datastore/{file_id}/content')
        self.assertEqual(b'new content', response.content)
