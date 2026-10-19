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

"""Resolution of AI suggestions: accept (executes as the current user,
once), dismiss, answer validation, wait resumption and sibling expiry.
The DB helpers, the engine and the tool executor are patched."""

import uuid
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.ai_suggestions import ai_suggestions_accept
from app.business.ai_suggestions import ai_suggestions_answer
from app.business.ai_suggestions import ai_suggestions_counts
from app.business.ai_suggestions import ai_suggestions_dismiss
from app.business.ai_suggestions import ai_suggestions_get
from app.business.ai_suggestions import ai_suggestions_list
from app.business.ai_suggestions import ai_suggestions_validate_answer
from app import app
from app.iris_engine.ai_workflows.tools import CLASSIFICATION_READ
from app.iris_engine.ai_workflows.tools import CLASSIFICATION_WRITE
from app.models.ai_workflows import EXEC_ACCEPTED_BY_USER
from app.models.ai_workflows import SUGGESTION_ACCEPTED
from app.models.ai_workflows import SUGGESTION_ACTION_DONE
from app.models.ai_workflows import SUGGESTION_ACTION_PENDING
from app.models.ai_workflows import SUGGESTION_ACTION_SKIPPED
from app.models.ai_workflows import SUGGESTION_DISMISSED
from app.models.ai_workflows import SUGGESTION_EXPIRED
from app.models.ai_workflows import SUGGESTION_GENERIC_ACTION
from app.models.ai_workflows import SUGGESTION_INFO_REQUEST
from app.models.ai_workflows import SUGGESTION_OPEN
from app.models.ai_workflows import WAIT_PENDING
from app.models.ai_workflows import WAIT_RESOLVED
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.ai_suggestions'
_ANALYST = 12
_RUN = SimpleNamespace(id=3, uuid=uuid.uuid4(), workflow_id=7, workflow_version=2)
_FORM = {'fields': [
    {'name': 'reason', 'type': 'text', 'required': True},
    {'name': 'score', 'type': 'number'},
    {'name': 'confirmed', 'type': 'boolean', 'required': True},
    {'name': 'severity', 'type': 'select', 'options': [{'value': 'low', 'label': 'Low'}, 'high']},
]}


def _suggestion(suggestion_id=1, kind=SUGGESTION_GENERIC_ACTION, wait_id=None, **overrides):
    values = {
        'id': suggestion_id,
        'kind': kind,
        'title': 'Tag the alert',
        'status': SUGGESTION_OPEN,
        'proposed_action': {'tool': 'update_alert', 'arguments': {'alert_id': 4, 'tags': 'phish'}},
        'proposed_actions': None,
        'form_schema': _FORM if kind == SUGGESTION_INFO_REQUEST else None,
        'wait_id': wait_id,
        'run': _RUN,
        'run_id': _RUN.id,
        'case_id': None,
        'alert_id': None,
        'war_room_id': None,
        'customer_id': None,
        'related_refs': None,
        'audience_user_ids': None,
        'entity_type': 'alert',
        'entity_id': 4,
        'resolved_by_id': None,
        'resolved_at': None,
        'resolution_note': None,
        'result': None,
        'result_tool_call_id': None,
        'answer': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _SuggestionsTestCase(TestCase):

    def setUp(self):
        self.suggestions = {}
        self.waits = {}
        self.visible = True
        self.execute = MagicMock(return_value={'ok': True, 'result': {'alert_id': 4}, 'error': None,
                                               'tool_call_id': 77})
        self.resume = MagicMock()
        self.hook = MagicMock()
        self.emit = MagicMock()
        self.track = MagicMock()
        self.commits = MagicMock()
        self.classification = MagicMock(return_value=CLASSIFICATION_WRITE)
        self.lock_wait = MagicMock(side_effect=lambda wait_id=None, **_kwargs: (None, self.waits.get(wait_id)))
        self.is_admin = False
        serialize = MagicMock(side_effect=lambda s: {'id': s.id, 'status': s.status, 'result': s.result,
                                                     'answer': s.answer, 'resolved_by_id': s.resolved_by_id})
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_get_suggestion',
                  side_effect=lambda i, **_kwargs: self.suggestions.get(i)),
            patch(f'{_BUSINESS}.ai_workflows_engine_lock_run_then_wait', self.lock_wait),
            patch(f'{_BUSINESS}.ai_workflows_tools_classification', self.classification),
            patch(f'{_BUSINESS}._is_admin', side_effect=lambda _user_id: self.is_admin),
            patch('app.business.ai_workflows.ai_workflows_suggestions_serialize', serialize),
            patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': True}),
            patch(f'{_BUSINESS}.ai_workflows_db_open_suggestions_for_wait',
                  side_effect=lambda wait_id: [s for s in self.suggestions.values()
                                               if s.wait_id == wait_id and s.status == SUGGESTION_OPEN]),
            patch(f'{_BUSINESS}.ai_workflows_db_open_suggestions_for_entities',
                  side_effect=lambda _entity_type, ids: [s for s in self.suggestions.values()
                                                        if s.entity_id in ids and s.status == SUGGESTION_OPEN]),
            patch(f'{_BUSINESS}.ai_workflows_db_commit', self.commits),
            patch(f'{_BUSINESS}.ai_workflows_db_rollback'),
            patch(f'{_BUSINESS}.ai_workflows_db_user_summary',
                  side_effect=lambda ids: {i: {'id': i, 'login': f'user{i}', 'name': f'User {i}'} for i in ids}),
            patch(f'{_BUSINESS}.ai_workflows_suggestions_user_can_see', side_effect=lambda _user_id, _suggestion: self.visible),
            patch(f'{_BUSINESS}.ai_workflows_suggestions_serialize', serialize),
            patch(f'{_BUSINESS}.ai_workflows_suggestions_emit', self.emit),
            patch(f'{_BUSINESS}.ai_workflows_tools_execute', self.execute),
            patch(f'{_BUSINESS}.ai_workflows_engine_resume_wait', self.resume),
            patch(f'{_BUSINESS}.call_modules_hook', self.hook),
            patch(f'{_BUSINESS}.track_activity', self.track),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _add(self, suggestion):
        self.suggestions[suggestion.id] = suggestion
        return suggestion

    def _add_wait(self, wait_id=50, status=WAIT_PENDING):
        wait = SimpleNamespace(id=wait_id, status=status)
        self.waits[wait_id] = wait
        return wait


class TestsAccept(_SuggestionsTestCase):

    def test_accept_should_execute_the_action_as_the_current_user(self):
        suggestion = self._add(_suggestion())
        ai_suggestions_accept(1, _ANALYST, note='ok')
        self.execute.assert_called_once()
        args, kwargs = self.execute.call_args
        self.assertEqual((_ANALYST, 'update_alert', {'alert_id': 4, 'tags': 'phish'}), args)
        self.assertEqual(EXEC_ACCEPTED_BY_USER, kwargs['execution_mode'])
        self.assertIs(suggestion, kwargs['suggestion'])
        self.assertEqual(SUGGESTION_ACCEPTED, suggestion.status)
        self.assertEqual(_ANALYST, suggestion.resolved_by_id)
        self.assertEqual('ok', suggestion.resolution_note)
        self.assertEqual({'alert_id': 4}, suggestion.result)
        self.assertEqual(77, suggestion.result_tool_call_id)

    def test_accept_should_fire_the_hook_and_emit_and_track(self):
        self._add(_suggestion())
        ai_suggestions_accept(1, _ANALYST)
        self.assertEqual('on_postload_ai_suggestion_accept', self.hook.call_args.args[0])
        self.emit.assert_called()
        self.track.assert_called()
        self.assertEqual(_ANALYST, self.track.call_args.kwargs['user_id_override'])

    def test_double_accept_should_execute_once(self):
        self._add(_suggestion())
        ai_suggestions_accept(1, _ANALYST)
        with self.assertRaises(BusinessProcessingError) as context:
            ai_suggestions_accept(1, _ANALYST)
        self.assertEqual({'status': SUGGESTION_ACCEPTED}, context.exception.get_data())
        self.execute.assert_called_once()

    def test_accept_should_claim_the_row_before_executing(self):
        suggestion = self._add(_suggestion())
        seen = {}

        def _execute(*_args, **_kwargs):
            seen['status'] = suggestion.status
            seen['commits'] = self.commits.call_count
            return {'ok': True, 'result': None, 'error': None, 'tool_call_id': 1}
        self.execute.side_effect = _execute
        ai_suggestions_accept(1, _ANALYST)
        self.assertEqual(SUGGESTION_ACCEPTED, seen['status'])
        self.assertGreaterEqual(seen['commits'], 1)

    def test_failed_action_should_leave_the_suggestion_open(self):
        suggestion = self._add(_suggestion())
        self.execute.return_value = {'ok': False, 'result': None, 'error': 'denied', 'tool_call_id': 78}
        with self.assertRaises(BusinessProcessingError) as context:
            ai_suggestions_accept(1, _ANALYST)
        self.assertEqual({'tool_call_id': 78}, context.exception.get_data())
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)
        self.assertIsNone(suggestion.resolved_by_id)
        self.assertEqual(78, suggestion.result_tool_call_id)
        self.resume.assert_not_called()
        self.hook.assert_not_called()

    def test_accept_should_resume_the_wait_and_expire_siblings(self):
        wait = self._add_wait()
        self._add(_suggestion(1, wait_id=wait.id))
        sibling = self._add(_suggestion(2, wait_id=wait.id))
        unrelated = self._add(_suggestion(3, wait_id=None))
        ai_suggestions_accept(1, _ANALYST)
        self.resume.assert_called_once()
        args, kwargs = self.resume.call_args
        self.assertIs(wait, args[0])
        self.assertTrue(args[1]['accepted'])
        self.assertEqual(1, args[1]['suggestion_id'])
        self.assertEqual(_ANALYST, kwargs['resolved_by_id'])
        self.assertEqual(SUGGESTION_EXPIRED, sibling.status)
        self.assertEqual(SUGGESTION_OPEN, unrelated.status)

    def test_accept_should_not_resume_an_already_resolved_wait(self):
        wait = self._add_wait(status=WAIT_RESOLVED)
        self._add(_suggestion(1, wait_id=wait.id))
        ai_suggestions_accept(1, _ANALYST)
        self.resume.assert_not_called()

    def test_read_tool_should_not_be_accepted(self):
        suggestion = self._add(_suggestion())
        self.classification.return_value = CLASSIFICATION_READ
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_accept(1, _ANALYST)
        self.execute.assert_not_called()
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)

    def test_info_request_cannot_be_accepted(self):
        self._add(_suggestion(kind=SUGGESTION_INFO_REQUEST))
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_accept(1, _ANALYST)
        self.execute.assert_not_called()

    def test_invisible_suggestion_should_be_not_found(self):
        self._add(_suggestion())
        self.visible = False
        with self.assertRaises(ObjectNotFoundError):
            ai_suggestions_accept(1, _ANALYST)
        with self.assertRaises(ObjectNotFoundError):
            ai_suggestions_get(1, _ANALYST)
        self.execute.assert_not_called()

    def test_suggestion_without_action_should_be_accepted_without_execution(self):
        suggestion = self._add(_suggestion(proposed_action=None))
        ai_suggestions_accept(1, _ANALYST)
        self.execute.assert_not_called()
        self.assertEqual(SUGGESTION_ACCEPTED, suggestion.status)


class TestsDismiss(_SuggestionsTestCase):

    def test_dismiss_should_resolve_and_fire_the_dismiss_hook(self):
        suggestion = self._add(_suggestion())
        ai_suggestions_dismiss(1, _ANALYST, note='false positive')
        self.assertEqual(SUGGESTION_DISMISSED, suggestion.status)
        self.assertEqual('false positive', suggestion.resolution_note)
        self.assertEqual('on_postload_ai_suggestion_dismiss', self.hook.call_args.args[0])
        self.execute.assert_not_called()

    def test_dismissed_info_request_should_leave_through_timeout(self):
        wait = self._add_wait()
        self._add(_suggestion(kind=SUGGESTION_INFO_REQUEST, wait_id=wait.id))
        ai_suggestions_dismiss(1, _ANALYST)
        self.assertEqual('timeout', self.resume.call_args.kwargs['port'])

    def test_dry_run_suggestion_should_be_dismissed_without_hook_or_resume(self):
        wait = self._add_wait()
        suggestion = self._add(_suggestion(kind=SUGGESTION_INFO_REQUEST, wait_id=wait.id, status='dry_run'))
        ai_suggestions_dismiss(1, _ANALYST, note='seen')
        self.assertEqual(SUGGESTION_DISMISSED, suggestion.status)
        self.assertEqual('seen', suggestion.resolution_note)
        self.hook.assert_not_called()
        self.resume.assert_not_called()

    def test_dismiss_twice_should_fail(self):
        self._add(_suggestion())
        ai_suggestions_dismiss(1, _ANALYST)
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_dismiss(1, _ANALYST)


def _bundle(*statuses):
    """A bundle of actions on alert 4, pending unless told otherwise."""
    statuses = statuses or (SUGGESTION_ACTION_PENDING,) * 3
    return [{'id': index + 1, 'label': f'Action {index + 1}', 'tool': f'tool_{index + 1}',
             'arguments': {'alert_id': 4, 'step': index + 1}, 'status': status, 'tool_call_id': None, 'error': None}
            for index, status in enumerate(statuses)]


class TestsAcceptBundle(_SuggestionsTestCase):

    def _bundled(self, *statuses, **overrides):
        return self._add(_suggestion(proposed_action=None, proposed_actions=_bundle(*statuses), **overrides))

    def _statuses(self, suggestion):
        return [a['status'] for a in suggestion.proposed_actions]

    def test_accept_should_run_every_pending_action_in_order(self):
        suggestion = self._bundled()
        self.execute.side_effect = [{'ok': True, 'result': {'n': n}, 'error': None, 'tool_call_id': 80 + n}
                                    for n in (1, 2, 3)]
        ai_suggestions_accept(1, _ANALYST)
        self.assertEqual(['tool_1', 'tool_2', 'tool_3'], [c.args[1] for c in self.execute.call_args_list])
        self.assertTrue(all(c.args[0] == _ANALYST for c in self.execute.call_args_list))
        self.assertEqual([SUGGESTION_ACTION_DONE] * 3, self._statuses(suggestion))
        self.assertEqual([81, 82, 83], [a['tool_call_id'] for a in suggestion.proposed_actions])
        self.assertEqual({'actions': {'1': {'n': 1}, '2': {'n': 2}, '3': {'n': 3}}}, suggestion.result)
        self.assertEqual(SUGGESTION_ACCEPTED, suggestion.status)

    def test_accept_should_run_only_the_selected_actions_and_skip_the_rest(self):
        suggestion = self._bundled()
        ai_suggestions_accept(1, _ANALYST, actions=[3, '1'])
        self.assertEqual(['tool_1', 'tool_3'], [c.args[1] for c in self.execute.call_args_list])
        self.assertEqual([SUGGESTION_ACTION_DONE, SUGGESTION_ACTION_SKIPPED, SUGGESTION_ACTION_DONE],
                         self._statuses(suggestion))
        self.assertEqual(SUGGESTION_ACCEPTED, suggestion.status)

    def test_accept_should_tell_the_run_which_actions_ran(self):
        wait = self._add_wait()
        self._bundled(wait_id=wait.id)
        ai_suggestions_accept(1, _ANALYST, actions=[2])
        payload = self.resume.call_args.args[1]
        self.assertTrue(payload['accepted'])
        self.assertEqual([SUGGESTION_ACTION_SKIPPED, SUGGESTION_ACTION_DONE, SUGGESTION_ACTION_SKIPPED],
                         [a['status'] for a in payload['actions']])
        self.assertNotIn('arguments', payload['actions'][0])
        self.assertNotIn('result', payload)

    def test_failure_should_stop_keep_the_done_actions_and_reopen(self):
        suggestion = self._bundled()
        self.execute.side_effect = [{'ok': True, 'result': {'n': 1}, 'error': None, 'tool_call_id': 81},
                                    {'ok': False, 'result': None, 'error': 'denied', 'tool_call_id': 82}]
        with self.assertRaises(BusinessProcessingError) as context:
            ai_suggestions_accept(1, _ANALYST)
        self.assertEqual(2, self.execute.call_count)
        self.assertEqual({'tool_call_id': 82, 'action_id': 2, 'done': [1]}, context.exception.get_data())
        self.assertIn('Action 2', str(context.exception))
        self.assertEqual([SUGGESTION_ACTION_DONE, SUGGESTION_ACTION_PENDING, SUGGESTION_ACTION_PENDING],
                         self._statuses(suggestion))
        self.assertEqual('denied', suggestion.proposed_actions[1]['error'])
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)
        self.assertIsNone(suggestion.resolved_by_id)
        # Results go to the analyst once the whole accept succeeded
        self.assertIsNone(suggestion.result)
        self.resume.assert_not_called()
        self.hook.assert_not_called()
        self.emit.assert_called()

    def test_retry_should_only_offer_what_is_still_pending(self):
        suggestion = self._bundled(SUGGESTION_ACTION_DONE, SUGGESTION_ACTION_PENDING, SUGGESTION_ACTION_PENDING)
        with self.assertRaises(BusinessProcessingError) as context:
            ai_suggestions_accept(1, _ANALYST, actions=[1])
        self.assertIn('1', context.exception.get_data())
        self.execute.assert_not_called()
        ai_suggestions_accept(1, _ANALYST)
        self.assertEqual(['tool_2', 'tool_3'], [c.args[1] for c in self.execute.call_args_list])
        self.assertEqual([SUGGESTION_ACTION_DONE] * 3, self._statuses(suggestion))

    def test_invalid_selection_should_be_refused_before_anything_runs(self):
        suggestion = self._bundled()
        for selection in ([], [9], 'all', [True], [None]):
            with self.subTest(selection=selection), self.assertRaises(BusinessProcessingError):
                ai_suggestions_accept(1, _ANALYST, actions=selection)
        self.execute.assert_not_called()
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)

    def test_an_action_outside_the_suggestion_should_block_the_whole_accept(self):
        suggestion = self._add(_suggestion(proposed_action=None, proposed_actions=_bundle()))
        suggestion.proposed_actions[2]['arguments'] = {'alert_id': 5}
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_accept(1, _ANALYST)
        self.execute.assert_not_called()
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)

    def test_selection_on_a_single_action_suggestion_should_be_refused(self):
        self._add(_suggestion())
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_accept(1, _ANALYST, actions=[1])
        self.execute.assert_not_called()

    def test_dismiss_should_skip_the_pending_actions(self):
        suggestion = self._bundled(SUGGESTION_ACTION_DONE, SUGGESTION_ACTION_PENDING, SUGGESTION_ACTION_PENDING)
        ai_suggestions_dismiss(1, _ANALYST)
        self.assertEqual([SUGGESTION_ACTION_DONE, SUGGESTION_ACTION_SKIPPED, SUGGESTION_ACTION_SKIPPED],
                         self._statuses(suggestion))
        self.assertEqual(SUGGESTION_DISMISSED, suggestion.status)
        self.execute.assert_not_called()


class TestsAnswer(_SuggestionsTestCase):

    def test_answer_should_resume_the_wait_on_the_answered_port(self):
        wait = self._add_wait()
        suggestion = self._add(_suggestion(kind=SUGGESTION_INFO_REQUEST, wait_id=wait.id))
        ai_suggestions_answer(1, _ANALYST, {'reason': 'known host', 'confirmed': False, 'score': '3'})
        self.assertEqual(SUGGESTION_ACCEPTED, suggestion.status)
        self.assertEqual({'reason': 'known host', 'confirmed': False, 'score': 3}, suggestion.answer)
        args, kwargs = self.resume.call_args
        self.assertEqual('answered', kwargs['port'])
        self.assertEqual(suggestion.answer, args[1]['answer'])
        self.assertEqual(_ANALYST, args[1]['answered_by']['id'])

    def test_invalid_answer_should_not_resolve(self):
        suggestion = self._add(_suggestion(kind=SUGGESTION_INFO_REQUEST, wait_id=self._add_wait().id))
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_answer(1, _ANALYST, {'confirmed': True})
        self.assertEqual(SUGGESTION_OPEN, suggestion.status)
        self.resume.assert_not_called()

    def test_answer_on_an_action_suggestion_should_fail(self):
        self._add(_suggestion())
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_answer(1, _ANALYST, {'reason': 'x', 'confirmed': True})


class TestsValidateAnswer(TestCase):

    def _errors(self, answer, form=_FORM):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_suggestions_validate_answer(form, answer)
        return context.exception.get_data()

    def test_valid_answer_should_be_cleaned(self):
        cleaned = ai_suggestions_validate_answer(_FORM, {'reason': 'r', 'confirmed': True, 'severity': 'low',
                                                         'score': 2.5})
        self.assertEqual({'reason': 'r', 'confirmed': True, 'severity': 'low', 'score': 2.5}, cleaned)

    def test_required_fields_should_be_enforced(self):
        errors = self._errors({'reason': '  '})
        self.assertIn('reason', errors)
        self.assertIn('confirmed', errors)

    def test_false_should_satisfy_a_required_boolean(self):
        cleaned = ai_suggestions_validate_answer(_FORM, {'reason': 'r', 'confirmed': False})
        self.assertFalse(cleaned['confirmed'])

    def test_types_should_be_enforced(self):
        errors = self._errors({'reason': 5, 'confirmed': 'yes', 'score': 'many', 'severity': 'critical'})
        self.assertEqual({'reason', 'confirmed', 'score', 'severity'}, set(errors))

    def test_unknown_fields_should_be_rejected(self):
        errors = self._errors({'reason': 'r', 'confirmed': True, 'extra': 1})
        self.assertEqual(['Unknown field'], errors['extra'])

    def test_non_object_answer_should_be_rejected(self):
        self.assertIn('answer', self._errors(['r']))

    def test_list_form_schema_should_be_accepted(self):
        cleaned = ai_suggestions_validate_answer([{'name': 'x', 'type': 'text'}], {'x': 'v'})
        self.assertEqual({'x': 'v'}, cleaned)


class TestsCounts(_SuggestionsTestCase):

    def test_counts_should_include_zeros(self):
        self._add(_suggestion(1, entity_id=4))
        self._add(_suggestion(2, entity_id=4))
        self.assertEqual({'4': 2, '5': 0}, ai_suggestions_counts(_ANALYST, 'alert', ['4', '5']))

    def test_counts_should_skip_invisible_suggestions(self):
        self._add(_suggestion(1, entity_id=4))
        self.visible = False
        self.assertEqual({'4': 0}, ai_suggestions_counts(_ANALYST, 'alert', [4]))

    def test_counts_should_reject_bad_ids(self):
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_counts(_ANALYST, 'alert', ['x'])


class TestsInboxList(_SuggestionsTestCase):

    def _list(self, **kwargs):
        with patch(f'{_BUSINESS}.ai_workflows_db_list_suggestions', return_value=[_suggestion()]) as listing:
            listed = ai_suggestions_list(_ANALYST, **kwargs)
        return listed, listing.call_args.kwargs

    def test_analyst_should_only_get_what_is_addressed_to_them(self):
        _listed, query = self._list()
        self.assertEqual(_ANALYST, query['audience_user_id'])
        self.assertTrue(query['with_unaddressed'])

    def test_mine_should_exclude_unaddressed(self):
        self.is_admin = True
        _listed, query = self._list(mine=True)
        self.assertEqual(_ANALYST, query['audience_user_id'])
        self.assertFalse(query['with_unaddressed'])

    def test_administrator_should_get_everything(self):
        self.is_admin = True
        _listed, query = self._list(status='all')
        self.assertNotIn('audience_user_id', query)
        self.assertIsNone(query['statuses'])

    def test_filters_should_reach_the_query(self):
        _listed, query = self._list(workflow_id=7, severity='high', entity_type='none', limit=10000,
                                    status='dry_run')
        self.assertEqual(7, query['workflow_id'])
        self.assertEqual('high', query['severity'])
        self.assertTrue(query['without_entity'])
        self.assertIsNone(query['entity_type'])
        self.assertEqual(500, query['limit'])
        self.assertEqual(['dry_run'], query['statuses'])

    def test_several_statuses_should_reach_the_query(self):
        _listed, query = self._list(status='open, dry_run,open')
        self.assertEqual(['open', 'dry_run'], query['statuses'])

    def test_unknown_status_should_be_refused(self):
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_list(_ANALYST, status='open,pending')

    def test_unknown_severity_should_be_refused(self):
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_list(_ANALYST, severity='urgent')


class TestsInboxPaging(_SuggestionsTestCase):
    """A fake table honouring the keyset and the limit, newest first."""

    def setUp(self):
        super().setUp()
        start = datetime(2026, 10, 1, 12, 0, 0)
        # Two rows share a timestamp: the id breaks the tie
        self.rows = [_suggestion(i, created_at=start + timedelta(seconds=i // 2)) for i in range(1, 13)]
        self.hidden = set()
        self.queries = []
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_list_suggestions', side_effect=self._query),
            patch(f'{_BUSINESS}.ai_workflows_suggestions_user_can_see',
                  side_effect=lambda _user_id, s: s.id not in self.hidden),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _query(self, limit=200, before=None, **_kwargs):
        self.queries.append(before)
        rows = sorted(self.rows, key=lambda s: (s.created_at, s.id), reverse=True)
        if before is not None:
            rows = [s for s in rows if (s.created_at, s.id) < before]
        return rows[:limit]

    def _ids(self, page):
        return [s['id'] for s in page['data']]

    def test_pages_should_walk_every_row_once(self):
        seen = []
        cursor = None
        for _ in range(10):
            page = ai_suggestions_list(_ANALYST, limit=5, cursor=cursor)
            seen.extend(self._ids(page))
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(list(range(12, 0, -1)), seen)

    def test_last_page_should_have_no_cursor(self):
        page = ai_suggestions_list(_ANALYST, limit=50)
        self.assertEqual(12, len(page['data']))
        self.assertIsNone(page['next_cursor'])

    def test_hidden_rows_should_not_shorten_a_page(self):
        self.hidden = {12, 11, 9}
        page = ai_suggestions_list(_ANALYST, limit=4)
        self.assertEqual([10, 8, 7, 6], self._ids(page))
        following = ai_suggestions_list(_ANALYST, limit=4, cursor=page['next_cursor'])
        self.assertEqual([5, 4, 3, 2], self._ids(following))

    def test_a_page_filled_inside_a_short_batch_should_keep_a_cursor(self):
        self.hidden = {12, 11, 10}
        page = ai_suggestions_list(_ANALYST, limit=5)
        self.assertEqual([9, 8, 7, 6, 5], self._ids(page))
        following = ai_suggestions_list(_ANALYST, limit=5, cursor=page['next_cursor'])
        self.assertEqual([4, 3, 2, 1], self._ids(following))
        self.assertIsNone(following['next_cursor'])

    def test_scan_should_stop_at_its_bound(self):
        self.hidden = {s.id for s in self.rows}
        with patch(f'{_BUSINESS}._MAX_LIST_SCAN', 6):
            page = ai_suggestions_list(_ANALYST, limit=4)
        self.assertEqual([], page['data'])
        self.assertIsNotNone(page['next_cursor'])
        following = ai_suggestions_list(_ANALYST, limit=4, cursor=page['next_cursor'])
        self.assertEqual([], following['data'])
        self.assertIsNone(following['next_cursor'])

    def test_bad_cursor_should_be_refused(self):
        with self.assertRaises(BusinessProcessingError):
            ai_suggestions_list(_ANALYST, cursor='yesterday')

    def test_unknown_run_should_give_an_empty_last_page(self):
        with patch(f'{_BUSINESS}.ai_workflows_db_get_run_by_uuid', return_value=None):
            page = ai_suggestions_list(_ANALYST, run_uuid=str(uuid.uuid4()))
        self.assertEqual({'data': [], 'next_cursor': None}, page)
