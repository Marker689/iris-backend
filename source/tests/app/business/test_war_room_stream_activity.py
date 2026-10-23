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

"""The war room's own activity in its stream: what each change records,
how the stream classifies it, and what it leaves out because a chat row
already shows it."""

import datetime
import re
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_chat
from app.business import war_room_note_folders
from app.business import war_room_notes
from app.business import war_room_tasks
from app.business import war_room_teams
from app.business import war_rooms
from app.business.war_room_chat import classify_war_room_activity_text


def _tracked(text):
    # As `track_activity` stores it
    return text[:1].upper() + text[1:]


def _mirrored(text):
    for pattern in war_room_chat._WAR_ROOM_ACTIVITY_MIRRORED:
        regex = '^' + '.*'.join(re.escape(part) for part in pattern.split('%')) + '$'
        if re.match(regex, text, re.IGNORECASE | re.DOTALL):
            return True
    return False


class _Recorder:
    """Patches `track_activity` of a module, keeps the texts."""

    def __init__(self, test, module):
        self.texts = []
        patcher = patch(f'{module}.track_activity',
                        side_effect=lambda message, **kwargs: self.texts.append((message, kwargs)))
        patcher.start()
        test.addCleanup(patcher.stop)

    @property
    def messages(self):
        return [message for message, _ in self.texts]


class TestsClassify(TestCase):

    def test_every_war_room_change_gets_its_family_and_verb(self):
        cases = {
            'created war room "Ransom"': 'room.created',
            'renamed war room "Ransom" to "Ransom 2"': 'room.renamed',
            'updated war room summary': 'room.summary',
            'set war room "Ransom" severity to High': 'room.severity',
            'moved war room "Ransom" to category "Crisis"': 'room.moved',
            'updated war room "Ransom"': 'room.updated',
            'added Alice to the war room as lead': 'member.added',
            'changed the war room role of Alice from responder to lead': 'member.updated',
            'removed Alice from the war room': 'member.removed',
            'updated war room member (user #3, role lead)': 'member.updated',
            'created war-room team "Blue"': 'team.created',
            'renamed war-room team "Blue" to "Red"': 'team.renamed',
            'deleted war-room team "Red"': 'team.deleted',
            'added Bob to war-room team "Red"': 'team.member_added',
            'removed Bob from war-room team "Red"': 'team.member_removed',
            'created war-room note folder "IOCs"': 'folder.created',
            'renamed war-room note folder "IOCs" to "Indicators"': 'folder.renamed',
            'moved war-room note folder "Indicators" to the root': 'folder.moved',
            'deleted war-room note folder "Indicators"': 'folder.deleted',
            'renamed war room note "Draft" to "Timeline"': 'note.renamed',
            'moved war room note "Timeline" to folder "IOCs"': 'note.moved',
            'updated war room note "Timeline"': 'note.updated',
            'deleted war room note "Timeline"': 'note.deleted',
            'restored revision #2 of note "Timeline"': 'note.updated',
            'shared war room note "Timeline" with cases (mirror)': 'share.created',
            'updated the sharing of war room folder "IOCs" with cases': 'share.updated',
            'stopped sharing war room note "Timeline" with cases': 'share.deleted',
            'renamed war room task "A" to "B"': 'task.renamed',
            'moved war room task "B" to "In progress"': 'task.moved',
            'assigned war room task "B" to Alice': 'task.assigned',
            'unassigned war room task "B"': 'task.unassigned',
            'updated war room task "B"': 'task.updated',
            'deleted war room task "B"': 'task.deleted',
            'unlinked war room task "B" from case #4': 'task.deleted',
            'created war room timeline "Main"': 'timeline.created',
            'added timeline event "Phishing"': 'event.created',
            'flagged timeline event "Phishing"': 'event.flagged',
            'uploaded file "dump.bin" (12 bytes) to datastore': 'datastore.created',
            'deleted file "dump.bin" from datastore': 'datastore.deleted',
            'drafted sitrep "Daily" (v1)': 'sitrep.created',
            'published sitrep "Daily" (v1)': 'sitrep.closed',
            'set the sitrep cadence to 60 minutes': 'sitrep.cadence',
            'updated decision D-3 "Isolate"': 'decision.updated',
            'voted approve on decision D-3': 'decision.voted',
            'deleted decision D-3': 'decision.deleted',
            'staged asset "srv-01"': 'scope.created',
            'changed the flags of 2 asset(s) from the war room scope': 'scope.updated',
            'detached registry asset #4 from the scope': 'scope.deleted',
            'tracked vulnerability #12': 'vulnerability.created',
            'stopped tracking vulnerability #12': 'vulnerability.deleted',
        }
        for text, slug in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slug, classify_war_room_activity_text(_tracked(text)))

    def test_anything_else_is_other(self):
        for text in (None, '', 42, 'Did something'):
            self.assertEqual('room.other', classify_war_room_activity_text(text))


class TestsMirrored(TestCase):

    def test_changes_the_stream_already_shows_are_left_out(self):
        for text in ('created war room task "B"', 'closed war room task "B"', 'reopened war room task "B"',
                     'fanned out war room task "B" to 2 case(s)', 'created war room note "N"',
                     'created decision D-3 "Isolate"', 'implemented decision D-3',
                     'changed war room "R" state from open to active',
                     'archived war room "R"', 'unarchived war room "R"'):
            with self.subTest(text=text):
                self.assertTrue(_mirrored(_tracked(text)))

    def test_other_changes_are_kept(self):
        for text in ('added Alice to the war room as lead', 'renamed war room note "A" to "B"',
                     'created war-room note folder "IOCs"', 'updated war room task "B"',
                     'created war room "R"', 'updated decision D-3 "Isolate"', 'reopened decision D-3',
                     'created war room timeline "Main"'):
            with self.subTest(text=text):
                self.assertFalse(_mirrored(_tracked(text)))


class TestsStreamRows(TestCase):

    def test_the_activity_of_the_war_room_becomes_war_room_activity_rows(self):
        row = SimpleNamespace(id=7, user_id=2, case_id=None, activity_date=datetime.datetime(2026, 1, 1),
                              activity_desc='Added Alice to the war room as lead',
                              user_login='bob', user_name='Bob')
        with patch('app.business.war_room_chat._build_war_room_activity_query', return_value=[row]) as query:
            rows = war_room_chat._fetch_war_room_activities(3, None, 50, search='alice')
        query.assert_called_once_with(3, None, 'alice', 50,
                                      excluded_patterns=war_room_chat._WAR_ROOM_ACTIVITY_MIRRORED)
        self.assertEqual(1, len(rows))
        self.assertEqual(-7, rows[0].message_id)
        self.assertEqual('war_room_activity', rows[0].kind)
        self.assertEqual('member.added', rows[0].activity_type)
        self.assertIsNone(rows[0].ref_case_id)
        self.assertEqual('Bob', rows[0].author_name)

    def test_the_kind_is_known(self):
        self.assertEqual('war_room_activity', war_room_chat._validate_kind('war_room_activity'))


class TestsMemberTexts(TestCase):

    def setUp(self):
        self.recorder = _Recorder(self, 'app.business.war_rooms')
        for target, value in (('war_room_user_label', lambda _user_id: 'Alice'),
                              ('call_modules_hook', MagicMock()), ('db', MagicMock())):
            patcher = patch(f'app.business.war_rooms.{target}', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('app.business.war_rooms_access.set_user_war_room_access')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add(self, existing, role='lead'):
        with patch('app.business.war_rooms.WarRoomMember') as model:
            model.query.filter_by.return_value.first.return_value = existing
            war_rooms.war_room_add_member(3, 9, role=role)
        return self.recorder.messages

    def test_a_new_member_is_named_with_their_role(self):
        self.assertEqual(['added Alice to the war room as lead'], self._add(None))

    def test_a_role_change_says_from_what_to_what(self):
        self.assertEqual(['changed the war room role of Alice from responder to lead'],
                         self._add(SimpleNamespace(role='responder')))

    def test_re_adding_with_the_same_role_records_nothing(self):
        self.assertEqual([], self._add(SimpleNamespace(role='lead')))

    def test_a_removed_member_is_named(self):
        with patch('app.business.war_rooms.WarRoomMember') as model, \
                patch('app.business.war_rooms_access.remove_user_war_room_access'):
            model.query.filter_by.return_value.delete.return_value = 1
            war_rooms.war_room_remove_member(3, 9)
            model.query.filter_by.return_value.delete.return_value = 0
            war_rooms.war_room_remove_member(3, 9)
        self.assertEqual(['removed Alice from the war room'], self.recorder.messages)


class TestsUserLabel(TestCase):

    def test_name_then_login_then_id(self):
        with patch('app.business.war_rooms.get_user', return_value=SimpleNamespace(name='Alice', user='alice')):
            self.assertEqual('Alice', war_rooms.war_room_user_label(9))
        with patch('app.business.war_rooms.get_user', return_value=SimpleNamespace(name='', user='alice')):
            self.assertEqual('alice', war_rooms.war_room_user_label(9))
        with patch('app.business.war_rooms.get_user', return_value=None):
            self.assertEqual('user #9', war_rooms.war_room_user_label(9))


class TestsWarRoomUpdateTexts(TestCase):

    def setUp(self):
        self.recorder = _Recorder(self, 'app.business.war_rooms')
        self.room = SimpleNamespace(name='Ransom', state='open', description='d', severity_id=None,
                                    color=None, custom_attributes=None, closed_at=None, closed_by_id=None)
        for target, value in (('war_room_get', lambda _war_room_id: self.room),
                              ('call_modules_hook', lambda _name, obj, **_kwargs: obj),
                              ('db', MagicMock()),
                              ('get_severity_by_id', lambda _severity_id: SimpleNamespace(severity_name='High'))):
            patcher = patch(f'app.business.war_rooms.{target}', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('app.business.war_room_note_shares.war_room_note_shares_reconcile_safe')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_change_is_recorded_on_its_own(self):
        war_rooms.war_room_update(3, name='Ransom 2', state='active', description='new', severity_id=2)
        self.assertEqual([
            'renamed war room "Ransom" to "Ransom 2"',
            'changed war room "Ransom 2" state from open to active',
            'updated war room summary',
            'set war room "Ransom 2" severity to High',
        ], self.recorder.messages)

    def test_resending_the_same_values_records_nothing(self):
        war_rooms.war_room_update(3, name='Ransom', state='open', description='d')
        self.assertEqual([], self.recorder.messages)

    def test_other_fields_are_an_update(self):
        war_rooms.war_room_update(3, color='#aabbcc')
        self.assertEqual(['updated war room "Ransom"'], self.recorder.messages)


class TestsNoteTexts(TestCase):

    def setUp(self):
        self.recorder = _Recorder(self, 'app.business.war_room_notes')
        self.note = SimpleNamespace(note_id=5, war_room_id=3, title='Draft', content='body', folder_id=None)
        for target, value in (('war_room_note_get', lambda _war_room_id, _note_id: self.note),
                              ('_resolve_folder_id', lambda _war_room_id, folder_id: folder_id),
                              ('war_room_note_folders_label',
                               lambda folder_id: 'the root' if folder_id is None else 'folder "IOCs"'),
                              ('write_revision', MagicMock()), ('db', MagicMock()),
                              ('_fire_mention_notifications', MagicMock()), ('_sync_note_shares', MagicMock()),
                              ('call_modules_hook', lambda _name, obj, **_kwargs: obj)):
            patcher = patch(f'app.business.war_room_notes.{target}', value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_rename_move_and_edit_are_told_apart(self):
        war_room_notes.war_room_note_update(3, 5, title='Timeline', content='new body', folder_id=8)
        self.assertEqual([
            'renamed war room note "Draft" to "Timeline"',
            'moved war room note "Timeline" to folder "IOCs"',
            'updated war room note "Timeline"',
        ], self.recorder.messages)

    def test_a_move_to_the_root(self):
        self.note.folder_id = 8
        war_room_notes.war_room_note_update(3, 5, folder_id=None)
        self.assertEqual(['moved war room note "Draft" to the root'], self.recorder.messages)

    def test_a_save_changing_nothing_records_nothing(self):
        war_room_notes.war_room_note_update(3, 5, title='Draft', content='body')
        self.assertEqual([], self.recorder.messages)


class TestsFolderTexts(TestCase):

    def setUp(self):
        self.recorder = _Recorder(self, 'app.business.war_room_note_folders')
        for target, value in (('db', MagicMock()), ('_sync_note_shares', MagicMock()),
                              ('get_folder', lambda _folder_id: SimpleNamespace(name='Cases'))):
            patcher = patch(f'app.business.war_room_note_folders.{target}', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder = SimpleNamespace(war_room_id=3, name='IOCs', parent_id=None, updated_at=None)

    def test_rename_and_move(self):
        war_room_note_folders.war_room_note_folders_update(self.folder, name='Indicators', parent_id=4)
        self.assertEqual([
            'renamed war-room note folder "IOCs" to "Indicators"',
            'moved war-room note folder "Indicators" to folder "Cases"',
        ], self.recorder.messages)
        self.assertEqual(('Indicators', 4), (self.folder.name, self.folder.parent_id))

    def test_a_move_to_the_root(self):
        self.folder.parent_id = 4
        war_room_note_folders.war_room_note_folders_update(self.folder, parent_id=None)
        self.assertEqual(['moved war-room note folder "IOCs" to the root'], self.recorder.messages)
        self.assertIsNone(self.folder.parent_id)

    def test_nothing_given_changes_nothing(self):
        war_room_note_folders.war_room_note_folders_update(self.folder)
        self.assertEqual([], self.recorder.messages)

    def test_a_folder_created_in_another_is_said_so(self):
        self.folder.parent_id = 4
        war_room_note_folders.war_room_note_folders_create(self.folder)
        self.assertEqual(['created war-room note folder "IOCs" in folder "Cases"'], self.recorder.messages)


class TestsTaskTexts(TestCase):

    def setUp(self):
        self.recorder = _Recorder(self, 'app.business.war_room_tasks')
        self.task = SimpleNamespace(task_id=1, war_room_id=3, title='B', status_id=2, assignee_id=9,
                                    description=None, due_at=None, tags=None, parent_task_id=None,
                                    source_case_id=None, source_case_task_id=None)

    def _track(self, **changes):
        prior = war_room_tasks._activity_snapshot(self.task, False)
        for key, value in changes.items():
            setattr(self.task, key, value)
        with patch('app.business.war_room_tasks.db') as db, \
                patch('app.business.war_rooms.war_room_user_label', return_value='Alice'):
            db.session.get.return_value = SimpleNamespace(status_name='In progress')
            war_room_tasks._track_task_changes(self.task, prior, war_room_tasks._activity_snapshot(self.task, False))
        return self.recorder.messages

    def test_each_change_says_what_changed(self):
        self.assertEqual([
            'renamed war room task "B" to "C"',
            'moved war room task "C" to "In progress"',
            'assigned war room task "C" to Alice',
            'updated war room task "C"',
        ], self._track(title='C', status_id=3, assignee_id=4, description='more'))

    def test_unassigned(self):
        self.assertEqual(['unassigned war room task "B"'], self._track(assignee_id=None))

    def test_no_change_records_nothing(self):
        self.assertEqual([], self._track())


class TestsTeamTexts(TestCase):

    def setUp(self):
        self.recorder = _Recorder(self, 'app.business.war_room_teams')
        self.team = SimpleNamespace(team_id=4, name='Blue', description=None, color=None)
        for target, value in (('war_room_team_get', lambda _war_room_id, _team_id: self.team),
                              ('db', MagicMock()), ('call_modules_hook', lambda _name, obj, **_kwargs: obj),
                              ('_user_label', lambda _user_id: 'Bob'),
                              ('war_room_board_widgets_prune', MagicMock())):
            patcher = patch(f'app.business.war_room_teams.{target}', value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_team_changes_are_recorded_on_the_war_room(self):
        with patch('app.business.war_room_teams.WarRoomTeam') as model:
            model.query.filter.return_value.filter.return_value.filter.return_value.first.return_value = None
            war_room_teams.war_room_team_update(3, 4, name='Red', color='#aabbcc')
        war_room_teams.war_room_team_delete(3, 4)
        self.assertEqual(['renamed war-room team "Blue" to "Red"', 'updated war-room team "Red"',
                          'deleted war-room team "Red"'], self.recorder.messages)
        self.assertTrue(all(kwargs == {'war_room_id': 3} for _, kwargs in self.recorder.texts))

    def test_team_members_are_named(self):
        with patch('app.business.war_room_teams.WarRoomMember') as members, \
                patch('app.business.war_room_teams.WarRoomTeamMember') as team_members:
            members.query.filter.return_value.filter.return_value.first.return_value = SimpleNamespace()
            team_members.query.filter.return_value.filter.return_value.first.return_value = None
            war_room_teams.war_room_team_member_add(3, 4, 9)
            team_members.query.filter.return_value.filter.return_value.first.return_value = SimpleNamespace()
            war_room_teams.war_room_team_member_remove(3, 4, 9)
        self.assertEqual(['added Bob to war-room team "Blue"', 'removed Bob from war-room team "Blue"'],
                         self.recorder.messages)
