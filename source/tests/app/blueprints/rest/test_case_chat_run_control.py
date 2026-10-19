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

"""Unit tests for stopping and steering a running chat turn: the loop
stops where it is when asked, and user turns sent meanwhile reach the
model at the next step. The business layer is mocked."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

import gevent

from app.blueprints.rest.v2.case_chat import loop
from app.iris_engine.llm.providers.base import TextDelta
from app.iris_engine.llm.providers.base import ToolUseEnd


_LOOP = 'app.blueprints.rest.v2.case_chat.loop'


class _Run:
    """Stand-in for `_RunControl` whose stop request flips on demand."""

    def __init__(self, stop_after_checks=None):
        self.interrupted = False
        self.checks = 0
        self._stop_after = stop_after_checks
        self.queued = []
        self.finished = []

    def check(self, force=False):
        self.checks += 1
        if self._stop_after is not None and self.checks > self._stop_after:
            self.interrupted = True
        return self.interrupted

    def take_queued_texts(self):
        texts, self.queued = self.queued, []
        return texts

    def finish(self):
        texts = self.finished.pop(0) if self.finished else []
        if texts:
            self.interrupted = False
        return texts


def _conversation():
    return SimpleNamespace(id=7, user_id=3, case_id=None, war_room_id=None,
                           resolved_policy_id=None, auto_approve_write_tools=False)


class TestInterruptibleEvents(TestCase):

    def setUp(self):
        patcher = patch(f'{_LOOP}._POLL_INTERVAL_SECONDS', 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_relays_the_provider_stream(self):
        events = [TextDelta(text='a'), TextDelta(text='b')]
        self.assertEqual(events, list(loop._interruptible_events(iter(events), _Run())))

    def test_stops_while_the_provider_is_silent(self):
        def _silent():
            gevent.sleep(30)
            yield TextDelta(text='late')

        with gevent.Timeout(2):
            relayed = list(loop._interruptible_events(_silent(), _Run(stop_after_checks=3)))
        self.assertEqual([], relayed)

    def test_reraises_provider_errors(self):
        def _failing():
            yield TextDelta(text='a')
            raise RuntimeError('provider down')

        with self.assertRaises(RuntimeError):
            list(loop._interruptible_events(_failing(), _Run()))


class TestStoppedStream(TestCase):

    def _drain(self, events, run):
        biz = MagicMock()
        with patch(f'{_LOOP}.case_chat_biz', biz):
            result = loop._drain_stream(
                events=iter(events), text_buffer=[], tool_uses=[],
                conversation=_conversation(), audit_row=MagicMock(),
                run=run, emit=MagicMock(),
            )
        return result, biz

    def test_keeps_the_text_streamed_so_far_without_its_tool_calls(self):
        run = _Run()
        run.interrupted = True
        result, biz = self._drain([
            TextDelta(text='Looking at the IOCs'),
            ToolUseEnd(tool_use_id='tu1', name='iris_iocs_list', arguments={}),
        ], run)
        self.assertTrue(result.interrupted)
        biz.append_message.assert_called_once()
        self.assertEqual([{'type': 'text', 'text': 'Looking at the IOCs'}],
                         biz.append_message.call_args.kwargs['content'])

    def test_persists_nothing_when_stopped_before_any_text(self):
        run = _Run()
        run.interrupted = True
        result, biz = self._drain([], run)
        self.assertTrue(result.interrupted)
        biz.append_message.assert_not_called()


class TestStoppedBetweenTools(TestCase):

    def _finalise(self, tool_uses, run):
        biz = MagicMock()
        cfg = SimpleNamespace(auto_execute_read_tools=True, allow_auto_approve_write_tools=False)
        with patch(f'{_LOOP}.case_chat_biz', biz), \
                patch('app.iris_engine.llm.client.load_config', return_value=cfg), \
                patch(f'{_LOOP}.tool_classification.is_read_only', side_effect=lambda n: n.endswith('_list')), \
                patch(f'{_LOOP}.tool_classification.is_write', side_effect=lambda n: not n.endswith('_list')), \
                patch(f'{_LOOP}._dispatch_and_track', return_value={'ok': True}) as dispatch:
            result = loop._finalise_turn(
                text='', tool_uses=tool_uses, stop_reason='tool_use',
                conversation=_conversation(), run=run, emit=MagicMock(),
            )
        return result, biz, dispatch

    def test_tools_not_run_get_a_result_and_no_write_waits_for_approval(self):
        result, biz, dispatch = self._finalise([
            {'id': 'tu1', 'name': 'iris_iocs_list', 'input': {}},
            {'id': 'tu2', 'name': 'iris_iocs_list', 'input': {}},
            {'id': 'tu3', 'name': 'iris_cases_update', 'input': {}},
        ], _Run(stop_after_checks=1))
        self.assertTrue(result.interrupted)
        self.assertEqual(1, dispatch.call_count)
        biz.create_pending_tool_call.assert_not_called()
        answered = [c.kwargs['tool_use_id'] for c in biz.append_message.call_args_list
                    if c.kwargs.get('tool_use_id')]
        self.assertEqual(['tu1', 'tu2', 'tu3'], answered)
        not_run = [c.kwargs['content'][0]['content'] for c in biz.append_message.call_args_list
                   if c.kwargs.get('tool_use_id') in ('tu2', 'tu3')]
        self.assertEqual([loop._NOT_RUN_STOPPED] * 2, not_run)

    def test_writes_wait_for_approval_when_not_stopped(self):
        result, biz, _ = self._finalise(
            [{'id': 'tu1', 'name': 'iris_cases_update', 'input': {}}], _Run())
        self.assertTrue(result.paused_on_write)
        biz.create_pending_tool_call.assert_called_once()


class TestRunSteering(TestCase):

    def _run(self, run, turns, max_tool_calls=5):
        cfg = SimpleNamespace(enabled=True, daily_token_budget_per_user=0,
                              daily_token_budget_org=0, max_tool_calls_per_turn=max_tool_calls)
        live = SimpleNamespace(restriction_level=0)
        conversation = SimpleNamespace(**vars(_conversation()), resolved_restriction_level=0)
        emit = MagicMock()
        with patch(f'{_LOOP}.resolve_for_conversation', return_value=live), \
                patch(f'{_LOOP}.load_config', return_value=cfg), \
                patch(f'{_LOOP}.get_llm_provider'), \
                patch(f'{_LOOP}.iris_current_user'), \
                patch(f'{_LOOP}.case_chat_biz'), \
                patch(f'{_LOOP}.absorb_user_texts') as absorb, \
                patch(f'{_LOOP}._run_turn', side_effect=turns) as run_turn:
            loop._run(conversation=conversation, emit=emit, run=run)
        events = [c.args[0] for c in emit.call_args_list]
        return absorb, run_turn, events

    def test_turn_queued_during_the_run_keeps_it_going(self):
        run = _Run()
        run.finished = [['also check the hashes']]
        absorb, run_turn, _ = self._run(run, [
            loop._TurnResult(terminated=True), loop._TurnResult(terminated=True)])
        self.assertEqual(2, run_turn.call_count)
        self.assertEqual(['also check the hashes'], absorb.call_args.kwargs['texts'])

    def test_turn_queued_between_steps_reaches_the_next_one(self):
        run = _Run()
        run.queued = ['use the last 24h only']
        absorb, run_turn, _ = self._run(run, [loop._TurnResult(terminated=True)])
        self.assertEqual(1, run_turn.call_count)
        self.assertEqual(['use the last 24h only'], absorb.call_args.kwargs['texts'])

    def test_stop_ends_the_run_and_tells_the_panel(self):
        run = _Run(stop_after_checks=1)
        _, run_turn, events = self._run(run, [loop._TurnResult()])
        self.assertEqual(1, run_turn.call_count)
        self.assertEqual(('assistant_end', 'interrupted'),
                         (events[-1].name, events[-1].payload['stop_reason']))

    def test_running_out_of_tool_calls_tells_the_panel(self):
        _, run_turn, events = self._run(_Run(), [loop._TurnResult()] * 2, max_tool_calls=2)
        self.assertEqual(2, run_turn.call_count)
        self.assertEqual(('assistant_end', 'max_tool_calls'),
                         (events[-1].name, events[-1].payload['stop_reason']))

    def test_errors_end_the_run_without_looking_at_the_queue(self):
        run = _Run()
        run.finish = MagicMock()
        self._run(run, [loop._TurnResult(errored=True)])
        run.finish.assert_not_called()


class TestAbsorbUserTexts(TestCase):

    def test_pending_writes_are_denied_before_the_new_turn(self):
        biz = MagicMock()
        biz.list_pending_tool_call_ids.return_value = [11]
        biz.resolve_pending_tool_call.return_value = (
            SimpleNamespace(tool_use_id='tu1', tool_name='iris_cases_update'), True)
        with patch(f'{_LOOP}.case_chat_biz', biz), patch(f'{_LOOP}.iris_current_user'):
            loop.absorb_user_texts(conversation=_conversation(), texts=['a', 'b'], emit=MagicMock())
        self.assertFalse(biz.resolve_pending_tool_call.call_args.kwargs['approve'])
        roles = [c.kwargs['role'] for c in biz.append_message.call_args_list]
        self.assertEqual(['tool', 'user'], roles)
        self.assertEqual([{'type': 'text', 'text': 'a\n\nb'}],
                         biz.append_message.call_args.kwargs['content'])


class TestRunControl(TestCase):

    def test_checks_are_rate_limited_unless_forced(self):
        with patch(f'{_LOOP}.case_chat_biz') as biz:
            biz.beat_run.return_value = False
            run = loop._RunControl(7, 'token')
            run.check()
            run.check()
            run.check(force=True)
        self.assertEqual(2, biz.beat_run.call_count)

    def test_a_stop_sticks(self):
        with patch(f'{_LOOP}.case_chat_biz') as biz:
            biz.beat_run.return_value = True
            run = loop._RunControl(7, 'token')
            self.assertTrue(run.check(force=True))
            biz.beat_run.return_value = False
            self.assertTrue(run.check(force=True))
