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

"""Unit tests for `ensure_snapshot`: a document is seeded once, under the
row lock, and the epoch it returns changes only when it is re-seeded.
Every collaborator is patched: no DB is needed."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from pycrdt import Doc
from pycrdt import XmlElement
from pycrdt import XmlFragment
from pycrdt import XmlText

from app.business import collab
from app.iris_engine.collab.render import append_markdown_to_ydoc_update
from app.iris_engine.collab.render import ydoc_update_is_legacy_tree
from app.iris_engine.collab.render import ydoc_update_to_markdown


_CURRENT = collab._CURRENT_SEEDER_VERSION
_LAST_TREE = collab._LAST_TREE_SEEDER_VERSION


def _row(y_state=b'stored', seeder_version=_CURRENT):
    return SimpleNamespace(y_state=y_state, seeder_version=seeder_version,
                           content_md=None, last_flushed_at=None)


class TestEnsureSnapshot(TestCase):

    def _ensure(self, unlocked_row, locked_row=None):
        db = MagicMock()
        doc_model = MagicMock()
        doc_model.query.filter_by.return_value.first.return_value = unlocked_row
        with patch.object(collab, 'CollabDoc', doc_model), \
                patch.object(collab, 'db', db), \
                patch.object(collab, 'markdown_to_ydoc_update', return_value=b'seed') as seeder, \
                patch.object(collab, 'collab_create_doc_if_missing') as create, \
                patch.object(collab, 'collab_lock_doc', return_value=locked_row) as lock:
            result = collab.ensure_snapshot('note:1', 'markdown')
        return result, SimpleNamespace(db=db, seeder=seeder, create=create, lock=lock)

    def test_seeded_doc_is_returned_without_a_lock(self):
        result, mocks = self._ensure(_row())
        self.assertEqual((b'stored', _CURRENT), result)
        mocks.lock.assert_not_called()
        mocks.create.assert_not_called()
        mocks.db.session.commit.assert_not_called()

    def test_first_open_seeds_the_row_it_creates_under_the_lock(self):
        created = _row(y_state=None, seeder_version=None)
        result, mocks = self._ensure(None, created)
        self.assertEqual((b'seed', _CURRENT), result)
        mocks.create.assert_called_once_with('note:1')
        mocks.lock.assert_called_once_with('note:1')
        mocks.seeder.assert_called_once_with('markdown')
        self.assertEqual(b'seed', created.y_state)
        self.assertEqual(_CURRENT, created.seeder_version)
        mocks.db.session.commit.assert_called_once()

    def test_stale_seeder_version_reseeds_with_a_new_epoch(self):
        stale = _row(seeder_version=_CURRENT - 1)
        result, mocks = self._ensure(stale, stale)
        self.assertEqual((b'seed', _CURRENT), result)
        self.assertEqual(b'seed', stale.y_state)
        mocks.db.session.commit.assert_called_once()

    def test_doc_seeded_by_a_concurrent_join_is_kept(self):
        # Seen stale before the lock, current once it is held: another join
        # seeded it meanwhile. Seeding again would replace the doc its
        # editors already hold with an unrelated one.
        seeded_meanwhile = _row(y_state=b'theirs')
        result, mocks = self._ensure(_row(seeder_version=_CURRENT - 1), seeded_meanwhile)
        self.assertEqual((b'theirs', _CURRENT), result)
        self.assertEqual(b'theirs', seeded_meanwhile.y_state)
        mocks.db.session.commit.assert_not_called()
        mocks.db.session.rollback.assert_called_once()


def _legacy_tree(*paragraphs):
    """A version-4 document: a ProseMirror tree, no text."""
    doc = Doc()
    frag = XmlFragment()
    doc['prosemirror'] = frag
    for paragraph in paragraphs:
        frag.children.append(XmlElement('paragraph')).children.append(XmlText(paragraph))
    return doc.get_update()


class TestLegacyTreeMigration(TestCase):
    """Version-4 rows hold a tree the CodeMirror editor can't bind to:
    the re-seed carries over the edits it holds that were not flushed."""

    def test_tree_with_unflushed_edits_seeds_from_the_tree(self):
        row = _row(_legacy_tree('flushed', 'typed since'), _LAST_TREE)
        row.content_md = 'flushed\n'
        self.assertEqual('flushed\n\ntyped since\n', collab._seed_markdown(row, 'flushed'))

    def test_tree_already_flushed_seeds_from_the_column(self):
        # The column keeps its own formatting: the tree render would be
        # flushed back over it as an edit nobody made.
        row = _row(_legacy_tree('same'), _LAST_TREE)
        row.content_md = 'same\n'
        self.assertEqual('* raw column', collab._seed_markdown(row, '* raw column'))

    def test_tree_converted_by_a_server_write_seeds_from_its_text(self):
        state = append_markdown_to_ydoc_update(_legacy_tree('typed'), 'appended')
        row = _row(state, _LAST_TREE)
        row.content_md = ydoc_update_to_markdown(state)
        self.assertEqual('typed\n\nappended', collab._seed_markdown(row, 'column'))

    def test_empty_tree_seeds_from_the_column(self):
        row = _row(_legacy_tree(), _LAST_TREE)
        row.content_md = 'stale'
        self.assertEqual('column', collab._seed_markdown(row, 'column'))

    def test_unreadable_state_seeds_from_the_column(self):
        self.assertEqual('column', collab._seed_markdown(_row(b'garbage', _LAST_TREE), 'column'))

    def test_older_versions_seed_from_the_column(self):
        row = _row(_legacy_tree('typed since'), _LAST_TREE - 1)
        self.assertEqual('column', collab._seed_markdown(row, 'column'))

    def test_first_open_seeds_from_the_column(self):
        self.assertEqual('column', collab._seed_markdown(None, 'column'))
        self.assertEqual('', collab._seed_markdown(None, None))

    def test_ensure_snapshot_reseeds_the_tree_as_text_with_a_new_epoch(self):
        row = _row(_legacy_tree('typed since'), _LAST_TREE)
        row.content_md = ''
        doc_model = MagicMock()
        doc_model.query.filter_by.return_value.first.return_value = row
        with patch.object(collab, 'CollabDoc', doc_model), \
                patch.object(collab, 'db', MagicMock()), \
                patch.object(collab, 'collab_create_doc_if_missing'), \
                patch.object(collab, 'collab_lock_doc', return_value=row):
            state, epoch = collab.ensure_snapshot('note:1', 'column')
        self.assertEqual(_CURRENT, epoch)
        self.assertFalse(ydoc_update_is_legacy_tree(state))
        self.assertEqual('typed since\n', ydoc_update_to_markdown(state))
        self.assertEqual('typed since\n', row.content_md)
