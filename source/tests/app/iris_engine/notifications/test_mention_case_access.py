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

"""A mention only notifies the users who can open what it was written in. No DB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.notifications.hook_listeners import _make_comment_listener
from app.iris_engine.notifications.hook_listeners import notifications_note_updated
from app.models.authorization import Permissions

_MODULE = 'app.iris_engine.notifications.hook_listeners'
_ACTOR = 1
_READER = 7
_OUTSIDER = 8
_CASE_IDENTIFIER = 4
_CUSTOMER_IDENTIFIER = 3


def _mentions(*user_ids):
    return ' '.join(f'<span data-mention data-kind="user" data-id="{user_id}" data-label="u">@u</span>'
                    for user_id in user_ids)


class _MentionTestCase(TestCase):

    def setUp(self):
        patch(f'{_MODULE}._actor_id', return_value=_ACTOR).start()
        self.notify_many = patch(f'{_MODULE}.notify_many').start()
        self.addCleanup(patch.stopall)

    def _notified(self):
        self.notify_many.assert_called_once()
        return set(self.notify_many.call_args.kwargs['user_ids'])


class TestCaseMentions(_MentionTestCase):

    def setUp(self):
        super().setUp()
        self.case_access = patch('app.business.access_controls.ac_fast_check_user_has_case_access',
                                 side_effect=lambda user_id, _case_id, _levels: 4 if user_id == _READER else None
                                 ).start()

    def test_note_mention_should_not_notify_a_user_without_case_access(self):
        note = SimpleNamespace(note_id=5, note_case_id=_CASE_IDENTIFIER, note_title='t',
                               note_content=_mentions(_READER, _OUTSIDER))
        notifications_note_updated(note, '')
        self.assertEqual({_READER}, self._notified())

    def test_note_mention_should_check_access_to_the_note_case(self):
        note = SimpleNamespace(note_id=5, note_case_id=_CASE_IDENTIFIER, note_title='t',
                               note_content=_mentions(_READER))
        notifications_note_updated(note, '')
        self.assertEqual(_CASE_IDENTIFIER, self.case_access.call_args.args[1])

    def test_note_mention_of_outsiders_only_should_notify_nobody(self):
        note = SimpleNamespace(note_id=5, note_case_id=_CASE_IDENTIFIER, note_title='t',
                               note_content=_mentions(_OUTSIDER))
        notifications_note_updated(note, '')
        self.notify_many.assert_not_called()

    def test_comment_mention_should_not_notify_a_user_without_case_access(self):
        payload = {
            'comment': SimpleNamespace(comment_id=9, comment_text=_mentions(_READER, _OUTSIDER)),
            'ioc': SimpleNamespace(ioc_id=2, case_id=_CASE_IDENTIFIER),
        }
        _make_comment_listener('ioc')(payload)
        self.assertEqual({_READER}, self._notified())


class TestAlertCommentMentions(_MentionTestCase):

    def setUp(self):
        super().setUp()
        self.permissions = {_READER: Permissions.alerts_read.value, _OUTSIDER: Permissions.alerts_read.value}
        self.members = {_READER}
        patch('app.datamgmt.manage.manage_users_db.get_user',
              side_effect=lambda user_id: SimpleNamespace(id=user_id)).start()
        patch('app.iris_engine.access_control.utils.ac_get_effective_permissions_of_user',
              side_effect=lambda user: self.permissions[user.id]).start()
        patch('app.datamgmt.manage.manage_access_control_db.user_has_client_access',
              side_effect=lambda user_id, customer_id: user_id in self.members
              and customer_id == _CUSTOMER_IDENTIFIER).start()

    def _comment(self, *user_ids):
        _make_comment_listener('alert')({
            'comment': SimpleNamespace(comment_id=9, comment_text=_mentions(*user_ids)),
            'alert': SimpleNamespace(alert_id=2, alert_customer_id=_CUSTOMER_IDENTIFIER),
        })

    def test_should_not_notify_a_user_outside_the_alert_customer(self):
        self._comment(_READER, _OUTSIDER)
        self.assertEqual({_READER}, self._notified())

    def test_should_not_notify_a_customer_member_without_alerts_read(self):
        self.permissions[_READER] = Permissions.standard_user.value
        self._comment(_READER)
        self.notify_many.assert_not_called()

    def test_should_notify_an_administrator_outside_the_customer(self):
        self.permissions[_OUTSIDER] = Permissions.alerts_read.value | Permissions.server_administrator.value
        self._comment(_OUTSIDER)
        self.assertEqual({_OUTSIDER}, self._notified())
