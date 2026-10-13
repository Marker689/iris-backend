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

"""Restoring a note revision goes through the collaborative document: the
state it replaces is the editor's, and the restored content reaches the
editor. Otherwise the editor kept the old content, and its next flush
wrote it back over the restore. Every collaborator is patched."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import notes
from app.business import war_room_notes


class TestNotesRestoreRevision(TestCase):

    def test_snapshots_the_live_content_then_pushes_the_restored_one(self):
        note = SimpleNamespace(note_id=4, note_case_id=2, note_title='Title', note_content='flushed')
        target = SimpleNamespace(note_title='Old title', note_content='restored')
        revisions = MagicMock()
        revisions.query.filter.return_value.first.return_value = target
        snapshots = []
        calls = []

        with patch.object(notes, 'get_note', return_value=note), \
                patch.object(notes, 'NoteRevisions', revisions), \
                patch.object(notes, 'notes_check_writable'), \
                patch.object(notes, 'iris_current_user', SimpleNamespace(id=7)), \
                patch.object(notes, 'track_activity'), \
                patch.object(notes, 'collab_current_markdown', return_value='live') as current, \
                patch.object(notes, 'update_note_revision',
                             side_effect=lambda _, n: snapshots.append(n.note_content)), \
                patch.object(notes, 'notes_update',
                             side_effect=lambda *_: calls.append('update')), \
                patch.object(notes, 'collab_replace_markdown',
                             side_effect=lambda *_: calls.append('replace')) as replace:
            notes.notes_restore_revision(4, 1)

        current.assert_called_once_with('note:4', 'flushed')
        self.assertEqual(['live'], snapshots)
        self.assertEqual('restored', note.note_content)
        self.assertEqual(['update', 'replace'], calls)
        replace.assert_called_once_with('note:4', 'restored')


class TestWarRoomNoteRestoreRevision(TestCase):

    def test_snapshots_the_live_content_then_pushes_the_restored_one(self):
        note = SimpleNamespace(note_id=8, title='Title', content='flushed')
        revision = SimpleNamespace(title='Old title', content='restored')
        snapshots = []
        calls = []
        db = MagicMock()
        db.session.commit.side_effect = lambda: calls.append('commit')

        with patch.object(war_room_notes, 'war_room_note_get', return_value=note), \
                patch.object(war_room_notes, 'get_revision', return_value=revision), \
                patch.object(war_room_notes, 'db', db), \
                patch.object(war_room_notes, 'track_activity'), \
                patch.object(war_room_notes, '_sync_note_shares'), \
                patch.object(war_room_notes, 'collab_current_markdown', return_value='live') as current, \
                patch.object(war_room_notes, 'write_revision',
                             side_effect=lambda _, n: snapshots.append(n.content)), \
                patch.object(war_room_notes, 'collab_replace_markdown',
                             side_effect=lambda *_: calls.append('replace')) as replace:
            war_room_notes.war_room_note_restore_revision(3, 8, 1, updated_by_id=7)

        current.assert_called_once_with('war-room-note:8', 'flushed')
        self.assertEqual(['live'], snapshots)
        self.assertEqual('restored', note.content)
        self.assertEqual(['commit', 'replace'], calls)
        replace.assert_called_once_with('war-room-note:8', 'restored')
