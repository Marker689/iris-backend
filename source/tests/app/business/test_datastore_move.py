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

"""The check that keeps a datastore folder from being moved beneath itself."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.datastore import datastore_is_within_node

_MODULE = 'app.business.datastore'


def _folder(path_id, path_parent_id):
    return SimpleNamespace(path_id=path_id, path_parent_id=path_parent_id)


class TestsDatastoreIsWithinNode(TestCase):

    def setUp(self):
        # root(1) ─ evidence(2) ─ disks(3) ─ images(4);  root(1) ─ notes(5)
        self._nodes = {
            1: _folder(1, 0),
            2: _folder(2, 1),
            3: _folder(3, 2),
            4: _folder(4, 3),
            5: _folder(5, 1),
        }
        patcher = patch(f'{_MODULE}.datastore_get_path_node',
                        side_effect=lambda node_id, _caseid: self._nodes.get(node_id))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_should_be_true_for_the_folder_itself(self):
        self.assertTrue(datastore_is_within_node(self._nodes[2], 2, 7))

    def test_should_be_true_for_a_direct_child(self):
        self.assertTrue(datastore_is_within_node(self._nodes[3], 2, 7))

    def test_should_be_true_for_a_deep_descendant(self):
        self.assertTrue(datastore_is_within_node(self._nodes[4], 2, 7))

    def test_should_be_false_for_a_sibling_branch(self):
        self.assertFalse(datastore_is_within_node(self._nodes[5], 2, 7))

    def test_should_be_false_for_an_ancestor(self):
        self.assertFalse(datastore_is_within_node(self._nodes[1], 2, 7))

    def test_should_stop_on_a_loop_already_in_the_data(self):
        self._nodes[2] = _folder(2, 3)
        self.assertFalse(datastore_is_within_node(self._nodes[3], 9, 7))
