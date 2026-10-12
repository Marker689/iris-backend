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

"""A thread reply lands on the topic of its root. No DB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.war_room_chat import create_reply

_MODULE = 'app.business.war_room_chat'
_WAR_ROOM_IDENTIFIER = 4
_ROOT_IDENTIFIER = 30
_TOPIC_IDENTIFIER = 7


class TestsReplyTopic(TestCase):

    def setUp(self):
        self.root = SimpleNamespace(message_id=_ROOT_IDENTIFIER, deleted_at=None, topic_id=_TOPIC_IDENTIFIER)
        patch(f'{_MODULE}._require_threads').start()
        patch(f'{_MODULE}._get_root_message', return_value=self.root).start()
        patch(f'{_MODULE}._resolve_attachments', return_value=None).start()
        patch(f'{_MODULE}._fire_reply_notifications').start()
        patch(f'{_MODULE}.call_modules_hook', side_effect=lambda _hook, message: message).start()
        self.session = patch(f'{_MODULE}.db.session').start()
        self.topics_supported = patch(f'{_MODULE}._topics_supported', return_value=True).start()
        self.addCleanup(patch.stopall)

    def _reply(self):
        return create_reply(_WAR_ROOM_IDENTIFIER, _ROOT_IDENTIFIER, 2, 'Seen on WS-042 too')

    def test_reply_should_take_the_topic_of_its_thread(self):
        reply = self._reply()
        self.assertEqual(_TOPIC_IDENTIFIER, reply.topic_id)
        self.assertEqual(_ROOT_IDENTIFIER, reply.parent_message_id)
        self.session.add.assert_called_once_with(reply)

    def test_reply_in_a_thread_on_main_should_stay_on_main(self):
        self.root.topic_id = None
        self.assertIsNone(self._reply().topic_id)

    def test_reply_should_not_set_a_topic_before_the_topics_migration(self):
        self.topics_supported.return_value = False
        reply = self._reply()
        self.assertIsNone(reply.topic_id)
