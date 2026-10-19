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

"""Unit tests for running Yuki's writes without the Approve/Deny card:
it takes both the analyst's opt-in on the conversation and the admin's
leave. The business layer is mocked."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.case_chat import loop


_LOOP = 'app.blueprints.rest.v2.case_chat.loop'


class _Run:
    interrupted = False

    def check(self, force=False):
        return False


class TestWriteAutoApprove(TestCase):

    def _finalise(self, *, allowed, opted_in):
        biz = MagicMock()
        cfg = SimpleNamespace(auto_execute_read_tools=True,
                              allow_auto_approve_write_tools=allowed)
        conversation = SimpleNamespace(
            id=7, user_id=3, case_id=None, war_room_id=None,
            resolved_policy_id=None, auto_approve_write_tools=opted_in)
        with patch(f'{_LOOP}.case_chat_biz', biz), \
                patch('app.iris_engine.llm.client.load_config', return_value=cfg), \
                patch(f'{_LOOP}.tool_classification.is_read_only', return_value=False), \
                patch(f'{_LOOP}.tool_classification.is_write', return_value=True), \
                patch(f'{_LOOP}._dispatch_and_track', return_value={'ok': True}) as dispatch:
            result = loop._finalise_turn(
                text='', tool_uses=[{'id': 'tu1', 'name': 'iris_iocs_create', 'input': {}}],
                stop_reason='tool_use', conversation=conversation, run=_Run(),
                emit=MagicMock(),
            )
        return result, biz, dispatch

    def test_writes_run_without_approval_when_opted_in_and_allowed(self):
        result, biz, dispatch = self._finalise(allowed=True, opted_in=True)
        self.assertFalse(result.paused_on_write)
        dispatch.assert_called_once()
        biz.create_pending_tool_call.assert_not_called()

    def test_writes_wait_for_approval_by_default_even_when_allowed(self):
        result, biz, dispatch = self._finalise(allowed=True, opted_in=False)
        self.assertTrue(result.paused_on_write)
        dispatch.assert_not_called()
        biz.create_pending_tool_call.assert_called_once()

    def test_writes_wait_for_approval_when_the_admin_disallows_it(self):
        result, biz, dispatch = self._finalise(allowed=False, opted_in=True)
        self.assertTrue(result.paused_on_write)
        dispatch.assert_not_called()
        biz.create_pending_tool_call.assert_called_once()
