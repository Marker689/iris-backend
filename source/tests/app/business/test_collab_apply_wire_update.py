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

"""Unit tests for `apply_wire_update`: the stored state is read under a
row lock and every update lands in it. The session is mocked, the
merges are real."""

import base64
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from pycrdt import Doc
from pycrdt import Text

from app.business import collab


def _b64(update):
    return base64.b64encode(update).decode()


def _text(state):
    doc = Doc()
    doc.apply_update(state)
    return str(doc.get('t', type=Text))


class TestApplyWireUpdate(TestCase):

    def setUp(self):
        self.doc = Doc()
        self.text = self.doc.get('t', type=Text)
        self.row = SimpleNamespace(y_state=self.doc.get_update(), updated_by_id=None, last_flushed_at=None)

    def _edit(self, typed):
        before = self.doc.get_state()
        self.text += typed
        return self.doc.get_update(before)

    def _apply(self, update_b64, row=None):
        db = MagicMock()
        row = self.row if row is None else row
        with patch.object(collab, 'collab_lock_doc', return_value=row) as lock, \
                patch.object(collab, 'db', db):
            result = collab.apply_wire_update('note:1', update_b64, 7)
        return result, db, lock

    def test_update_is_merged_under_the_lock_and_committed(self):
        update = self._edit('Hello')
        result, db, lock = self._apply(_b64(update))
        lock.assert_called_once_with('note:1')
        db.session.commit.assert_called_once()
        self.assertEqual(update, result)
        self.assertEqual('Hello', _text(self.row.y_state))
        self.assertEqual(7, self.row.updated_by_id)

    def test_successive_updates_all_render(self):
        # Each call merges into the state the previous one committed,
        # which the lock guarantees. Dropping the first update would
        # also hide the second: it builds on it.
        self._apply(_b64(self._edit('Hello ')))
        self._apply(_b64(self._edit('world')))
        self.assertEqual('Hello world', _text(self.row.y_state))

    def test_malformed_update_is_dropped_and_releases_the_lock(self):
        state = self.row.y_state
        result, db, _ = self._apply(_b64(b'\xff\xff\xff'))
        self.assertIsNone(result)
        self.assertEqual(state, self.row.y_state)
        db.session.rollback.assert_called_once()
        db.session.commit.assert_not_called()

    def test_uninitialised_doc_releases_the_lock(self):
        result, db, _ = self._apply(_b64(self._edit('Hello')), row=SimpleNamespace(y_state=None))
        self.assertIsNone(result)
        db.session.rollback.assert_called_once()

    def test_empty_or_undecodable_payload_takes_no_lock(self):
        for payload in ('', None, 'not base64!'):
            result, _, lock = self._apply(payload)
            self.assertIsNone(result)
            lock.assert_not_called()
