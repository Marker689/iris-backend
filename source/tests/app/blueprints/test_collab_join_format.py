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

"""Joining a collaborative doc: an editor that predates the markdown
document (a TipTap tab opened before the upgrade) is turned away, it
would bind a tree nothing reads any more."""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from flask import request

from app import app
from app.blueprints.socket_io_event_handlers import collab_event_handlers as handlers

_HANDLERS = 'app.blueprints.socket_io_event_handlers.collab_event_handlers'


class TestsJoinFormat(TestCase):

    def setUp(self):
        self.context = app.test_request_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        request.sid = 'sid-join-format'
        self.emit = MagicMock()
        self.joined = MagicMock()
        self.resolve = MagicMock(return_value={'exists': True, 'can_read': True, 'can_write': True,
                                               'current_content': 'md'})
        patchers = [
            patch(f'{_HANDLERS}._current_user_id', return_value=13),
            patch(f'{_HANDLERS}._current_user_name', return_value='alice'),
            patch(f'{_HANDLERS}.emit', self.emit),
            patch(f'{_HANDLERS}.join_room', self.joined),
            patch(f'{_HANDLERS}.resolve_doc', self.resolve),
            patch(f'{_HANDLERS}.ensure_snapshot', return_value=(b'state', 5)),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(handlers._sid_docs.pop, request.sid, None)

    def test_join_without_format_should_be_refused(self):
        handlers.on_join({'doc': 'note:1'})
        self.emit.assert_called_once_with('resolution-error',
                                          {'doc': 'note:1', 'reason': 'outdated editor: reload the page'})
        self.resolve.assert_not_called()
        self.joined.assert_not_called()

    def test_join_with_another_format_should_be_refused(self):
        handlers.on_join({'doc': 'note:1', 'format': 'prosemirror'})
        self.assertEqual('resolution-error', self.emit.call_args.args[0])
        self.joined.assert_not_called()

    def test_join_with_the_markdown_format_should_sync(self):
        handlers.on_join({'doc': 'note:1', 'format': 'markdown'})
        self.joined.assert_called_once_with('note:1')
        event, payload = self.emit.call_args.args
        self.assertEqual('sync-init', event)
        self.assertEqual(5, payload['epoch'])
