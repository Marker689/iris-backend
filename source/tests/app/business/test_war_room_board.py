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

"""Unit tests for the war-room board aggregation (no database)."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business import war_room_board as board
from app.business.war_room_board_widgets import war_room_board_widgets_default


_BIZ = 'app.business.war_room_board'
_NOW = datetime.datetime(2026, 10, 6, 12, 0)


def _flag(flag_id, kind, sort_order, name=None):
    return SimpleNamespace(id=flag_id, name=name or f'F{flag_id}', description=None, color='slate',
                           icon=None, kind=kind, sort_order=sort_order, requires_reason=False,
                           requires_decision=kind == 'exception')


def _assets(case_id, total, compromised=0, flagged=0):
    return SimpleNamespace(case_id=case_id, total=total, compromised=compromised, flagged=flagged)


def _per_flag(case_id, flag_id, total):
    return SimpleNamespace(case_id=case_id, flag_id=flag_id, total=total)


def _per_kind(case_id, kind, total):
    return SimpleNamespace(case_id=case_id, kind=kind, total=total)


def _decision_row(decision_id, number, status='proposed', target_at=None, pending=0):
    return SimpleNamespace(decision_id=decision_id, number=number, title=f'T{number}', status=status,
                           target_at=target_at, pending_approvers=pending)


def _case_row(case_id, name):
    return SimpleNamespace(case_id=case_id, case_name=name, customer_id=1, customer_name='ACME',
                           state_id=1, state_name='Open', owner_id=1, owner_name='admin',
                           severity_name='High', tasks_open=2, tasks_in_progress=1)


def _task_row(task_id, status_name, due_at=None, assignee_name=None, source_case_id=None):
    return SimpleNamespace(task_id=task_id, title=f'Task {task_id}', status_name=status_name, due_at=due_at,
                           assignee_name=assignee_name, source_case_id=source_case_id, parent_task_id=None)


_FLAGS = [_flag(1, 'status', 0, 'Isolated'), _flag(2, 'status', 1, 'Patched'),
          _flag(3, 'done', 2, 'Restored'), _flag(4, 'exception', 3, 'Accepted risk')]


class TestAggregateCounts(TestCase):

    def test_folds_rows_per_case(self):
        out = board._aggregate_counts(
            [_assets(10, 9, compromised=3, flagged=6), _assets(20, 1, flagged=1)],
            [_per_flag(10, 1, 4), _per_flag(10, 3, 4), _per_flag(20, 4, 1)],
            [_per_kind(10, 'status', 4), _per_kind(10, 'done', 4), _per_kind(20, 'exception', 1)],
        )
        self.assertEqual(out[10]['assets_total'], 9)
        self.assertEqual(out[10]['assets_compromised'], 3)
        # An asset carrying several flags counts under each of them.
        self.assertEqual(out[10]['by_flag'], {'none': 3, '1': 4, '3': 4})
        self.assertEqual(out[10]['by_kind'], {'none': 3, 'status': 4, 'done': 4, 'exception': 0})
        self.assertEqual(out[20]['by_kind'], {'none': 0, 'status': 0, 'done': 0, 'exception': 1})

    def test_unknown_kind_is_ignored(self):
        out = board._aggregate_counts([_assets(10, 1, flagged=1)], [], [_per_kind(10, 'progress', 1)])
        self.assertNotIn('progress', out[10]['by_kind'])


class TestAttention(TestCase):

    def test_rules_and_order(self):
        decisions = [
            _decision_row(1, 1, target_at=_NOW - datetime.timedelta(hours=1)),
            _decision_row(2, 2, status='approved', target_at=_NOW + datetime.timedelta(hours=2)),
            _decision_row(3, 3, target_at=_NOW + datetime.timedelta(days=3), pending=2),
            _decision_row(4, 4, status='approved', pending=1),
        ]
        compromised = [SimpleNamespace(asset_id=100, asset_name='DC01', case_id=10)]
        exceptions = [SimpleNamespace(asset_id=200, asset_name='OT-PLC', case_id=10, flag_name='Accepted risk')]
        items = board.war_room_board_attention(compromised, exceptions, decisions, _NOW)
        self.assertEqual([i['type'] for i in items], [
            'decision_overdue', 'compromised_unflagged',
            'decision_due_soon', 'exception_without_decision',
            'decision_pending_vote', 'decision_pending_implementation',
        ])
        self.assertEqual([i['severity'] for i in items], ['high', 'high', 'medium', 'medium', 'low', 'low'])
        self.assertEqual(items[0]['decision_id'], 1)
        self.assertEqual(items[1]['asset_id'], 100)
        self.assertEqual(items[1]['label'], 'DC01 is compromised and has no status flag')
        self.assertEqual(items[2]['decision_id'], 2)
        self.assertEqual(items[3]['label'], 'OT-PLC is flagged Accepted risk without a backing decision')
        self.assertEqual(items[4]['decision_id'], 3)
        self.assertIn('D-3', items[4]['label'])
        self.assertEqual(items[5]['decision_id'], 4)

    def test_every_open_decision_appears_once(self):
        decisions = [
            _decision_row(1, 1, target_at=_NOW - datetime.timedelta(hours=1), pending=2),
            _decision_row(2, 2),
            _decision_row(3, 3, pending=1),
        ]
        items = board.war_room_board_attention([], [], decisions, _NOW)
        self.assertEqual(sorted(i['decision_id'] for i in items), [1, 2, 3])
        self.assertEqual(items[0]['type'], 'decision_overdue')

    def test_proposed_without_approvers_awaits_approval(self):
        items = board.war_room_board_attention([], [], [_decision_row(5, 5)], _NOW)
        self.assertEqual(items[0]['type'], 'decision_pending_approval')
        self.assertEqual(items[0]['severity'], 'low')
        self.assertIn('D-5 awaits approval', items[0]['label'])

    def test_approved_decision_awaits_implementation(self):
        items = board.war_room_board_attention([], [], [_decision_row(4, 4, status='approved', pending=3)], _NOW)
        self.assertEqual(items[0]['type'], 'decision_pending_implementation')

    def test_limit_keeps_most_severe(self):
        low = [_decision_row(i, i, pending=1) for i in range(1, 6)]
        high = [_decision_row(9, 9, target_at=_NOW - datetime.timedelta(minutes=1))]
        items = board.war_room_board_attention([], [], low + high, _NOW, limit=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['type'], 'decision_overdue')


class TestBuild(TestCase):

    def _build(self, attached, accessible, asset_rows, case_rows, decision_rows=(), compromised=(),
               exceptions=(), vulnerabilities=None, include_vulnerabilities=True, flag_rows=(), kind_rows=(),
               task_rows=(), task_teams=None, tracker_access=None, trackers=None, widgets=None):
        vulnerabilities = vulnerabilities or {'exploited_open': [], 'overdue': []}
        calls = {}

        def _case_rows(case_ids):
            calls['case_rows'] = list(case_ids)
            return [r for r in case_rows if r.case_id in case_ids]

        def _filtered(name, rows):
            def _query(case_ids):
                calls[name] = list(case_ids)
                return [r for r in rows if r.case_id in case_ids]
            return _query

        def _compromised(case_ids, limit):
            calls['compromised'] = list(case_ids)
            return list(compromised)

        def _exceptions(case_ids, limit):
            calls['exceptions'] = list(case_ids)
            return list(exceptions)

        def _vuln_summary(case_ids):
            calls['vuln_summary'] = list(case_ids)
            return {'open': 3, 'exploited_open': 1, 'overdue': 2, 'kev_open': 0, 'assets_open': 2}

        def _trackers(war_room_id, access, room_widgets):
            calls['trackers'] = (access, room_widgets)
            return trackers or {'data': {}, 'steps': [], 'case_highlights': {}}

        def _tasks(war_room_id, limit, in_progress_only, team_ids):
            calls.setdefault('tasks', []).append((in_progress_only, team_ids))
            return list(task_rows)

        board_widgets = (widgets, True) if widgets is not None else (war_room_board_widgets_default(), False)

        def _vuln_attention(case_ids, limit):
            calls['vuln_attention'] = list(case_ids)
            return vulnerabilities

        with patch(f'{_BIZ}.war_room_board_db_attached_case_ids', return_value=list(attached)), \
                patch(f'{_BIZ}.war_room_board_db_flags', return_value=_FLAGS), \
                patch(f'{_BIZ}.war_room_board_db_case_rows', side_effect=_case_rows), \
                patch(f'{_BIZ}.war_room_board_db_asset_counts', side_effect=_filtered('counts', asset_rows)), \
                patch(f'{_BIZ}.war_room_board_db_flag_counts', side_effect=_filtered('flag_counts', flag_rows)), \
                patch(f'{_BIZ}.war_room_board_db_kind_counts', side_effect=_filtered('kind_counts', kind_rows)), \
                patch(f'{_BIZ}.war_room_board_db_open_decisions', return_value=list(decision_rows)), \
                patch(f'{_BIZ}.war_room_board_db_compromised_unflagged', side_effect=_compromised), \
                patch(f'{_BIZ}.war_room_board_db_exceptions_without_decision', side_effect=_exceptions), \
                patch(f'{_BIZ}.war_room_board_db_open_war_room_task_counts', return_value=(4, 1, 2)), \
                patch(f'{_BIZ}.war_room_board_db_open_war_room_tasks', side_effect=_tasks), \
                patch(f'{_BIZ}.war_room_task_teams', return_value=task_teams or {}), \
                patch(f'{_BIZ}.war_room_board_widgets_get', return_value=board_widgets), \
                patch(f'{_BIZ}.war_room_trackers_board_data', side_effect=_trackers), \
                patch(f'{_BIZ}.vulnerability_findings_summary', side_effect=_vuln_summary), \
                patch(f'{_BIZ}.vulnerability_findings_attention', side_effect=_vuln_attention):
            return board.war_room_board_build(7, accessible, now=_NOW,
                                              include_vulnerabilities=include_vulnerabilities,
                                              tracker_access=tracker_access), calls

    def test_kpis_over_accessible_cases_only(self):
        out, calls = self._build(
            attached=[10, 20],
            accessible=[10],
            asset_rows=[_assets(10, 7, compromised=2, flagged=5), _assets(20, 50, compromised=50)],
            flag_rows=[_per_flag(10, 2, 3), _per_flag(10, 3, 1), _per_flag(10, 4, 1), _per_flag(20, 1, 9)],
            kind_rows=[_per_kind(10, 'status', 3), _per_kind(10, 'done', 1), _per_kind(10, 'exception', 1),
                       _per_kind(20, 'done', 9)],
            case_rows=[_case_row(10, 'Visible'), _case_row(20, 'Secret')],
            decision_rows=[_decision_row(1, 1, target_at=_NOW - datetime.timedelta(hours=1)),
                           _decision_row(2, 2, target_at=_NOW + datetime.timedelta(hours=1)),
                           _decision_row(3, 3)],
        )
        self.assertEqual(calls['case_rows'], [10])
        self.assertEqual(calls['counts'], [10])
        self.assertEqual(calls['flag_counts'], [10])
        self.assertEqual(calls['kind_counts'], [10])
        self.assertEqual(calls['compromised'], [10])
        self.assertEqual(calls['exceptions'], [10])
        k = out['kpis']
        self.assertEqual(k['cases'], 2)
        self.assertEqual(k['cases_accessible'], 1)
        self.assertEqual(k['assets'], 7)
        self.assertEqual(k['compromised'], 2)
        self.assertEqual(k['flagged'], 5)
        self.assertEqual(k['done'], 1)
        self.assertEqual(k['exceptions'], 1)
        self.assertEqual(k['unflagged'], 2)
        self.assertEqual(k['decisions_open'], 3)
        self.assertEqual(k['decisions_overdue'], 1)
        self.assertEqual(k['decisions_due_24h'], 1)
        self.assertEqual(k['tasks_open'], 4)
        self.assertEqual(k['tasks_in_progress'], 1)
        self.assertEqual(k['tasks_overdue'], 2)
        self.assertEqual(out['generated_at'], _NOW.isoformat())
        self.assertEqual([f['name'] for f in out['flags']], ['Isolated', 'Patched', 'Restored', 'Accepted risk'])
        self.assertEqual(out['cases'][0]['by_flag'], {'none': 2, '2': 3, '3': 1, '4': 1})
        self.assertEqual([d['ref'] for d in out['decisions']], ['D-1', 'D-2', 'D-3'])
        self.assertEqual([d['overdue'] for d in out['decisions']], [True, False, False])
        self.assertEqual([d['due_soon'] for d in out['decisions']], [False, True, False])
        self.assertIsNone(out['decisions'][2]['target_at'])
        self.assertEqual(out['decisions'][0]['target_at'], (_NOW - datetime.timedelta(hours=1)).isoformat())

    def test_inaccessible_case_is_reduced_to_its_id(self):
        out, _ = self._build(attached=[10, 20], accessible=[10], asset_rows=[],
                             case_rows=[_case_row(10, 'Visible'), _case_row(20, 'Secret')])
        self.assertEqual(out['cases'][1], {'case_id': 20, 'accessible': False})
        self.assertEqual(out['cases'][0]['case_name'], 'Visible')
        self.assertTrue(out['cases'][0]['accessible'])
        self.assertNotIn('Secret', repr(out))

    def test_accessible_ids_not_attached_are_ignored(self):
        _, calls = self._build(attached=[10], accessible=[10, 99], asset_rows=[],
                               case_rows=[_case_row(10, 'Visible')])
        self.assertEqual(calls['case_rows'], [10])

    def test_case_without_assets_has_zero_counts(self):
        out, _ = self._build(attached=[10], accessible=[10], asset_rows=[], case_rows=[_case_row(10, 'V')])
        self.assertEqual(out['cases'][0]['assets_total'], 0)
        self.assertEqual(out['cases'][0]['by_flag'], {})
        self.assertEqual(out['cases'][0]['by_kind'], {'none': 0, 'status': 0, 'done': 0, 'exception': 0})
        self.assertEqual(out['cases'][0]['tasks_open'], 2)
        self.assertEqual(out['cases'][0]['tasks_in_progress'], 1)
        self.assertEqual(out['cases'][0]['highlights'], [])

    def test_no_access_at_all(self):
        out, calls = self._build(attached=[10], accessible=[], asset_rows=[_assets(10, 5)],
                                 case_rows=[_case_row(10, 'Secret')])
        self.assertEqual(calls['case_rows'], [])
        self.assertEqual(out['kpis']['assets'], 0)
        self.assertEqual(out['cases'], [{'case_id': 10, 'accessible': False}])

    def test_vulnerabilities_omitted_without_permission(self):
        exploited = {'finding_id': 5, 'asset_id': 8, 'asset_name': 'srv01', 'case_id': 10,
                     'identifier': 'CVE-2024-3400', 'severity': 'critical'}
        out, calls = self._build(attached=[10], accessible=[10], asset_rows=[], case_rows=[_case_row(10, 'V')],
                                 vulnerabilities={'exploited_open': [exploited], 'overdue': []},
                                 include_vulnerabilities=False)
        self.assertNotIn('vuln_summary', calls)
        self.assertNotIn('vuln_attention', calls)
        self.assertNotIn('vulnerabilities_open', out['kpis'])
        self.assertNotIn('vulnerable_assets', out['kpis'])
        self.assertNotIn('CVE-2024-3400', repr(out))

    def test_vulnerabilities_over_accessible_cases_only(self):
        exploited = {'finding_id': 5, 'asset_id': 8, 'asset_name': 'srv01', 'case_id': 10,
                     'identifier': 'CVE-2024-3400', 'severity': 'critical'}
        overdue = {'finding_id': 6, 'asset_id': 9, 'asset_name': 'srv02', 'case_id': 10,
                   'identifier': 'CVE-2023-4966', 'due_date': '2026-10-01'}
        out, calls = self._build(attached=[10, 20], accessible=[10], asset_rows=[],
                                 case_rows=[_case_row(10, 'Visible'), _case_row(20, 'Secret')],
                                 vulnerabilities={'exploited_open': [exploited], 'overdue': [overdue]})
        self.assertEqual(calls['vuln_summary'], [10])
        self.assertEqual(calls['vuln_attention'], [10])
        self.assertEqual(out['kpis']['vulnerabilities_open'], 3)
        self.assertEqual(out['kpis']['vulnerabilities_exploited_open'], 1)
        self.assertEqual(out['kpis']['vulnerable_assets'], 2)
        self.assertEqual([(i['type'], i['severity'], i['finding_id']) for i in out['attention']],
                         [('vulnerability_exploited_open', 'high', 5), ('vulnerability_overdue', 'medium', 6)])

    def test_default_board_lists_the_open_tasks_with_their_teams(self):
        out, calls = self._build(attached=[], accessible=[], asset_rows=[], case_rows=[],
                                 task_rows=[_task_row(1, 'In progress', due_at=_NOW - datetime.timedelta(hours=1),
                                                      assignee_name='alice'),
                                            _task_row(2, 'To do', due_at=_NOW + datetime.timedelta(hours=1)),
                                            _task_row(3, None, source_case_id=10)],
                                 task_teams={1: [{'team_id': 4, 'name': 'Comms', 'color': 'sky'}]})
        self.assertFalse(out['board_customized'])
        self.assertEqual([w['id'] for w in out['widgets']], [w['id'] for w in war_room_board_widgets_default()])
        data = next(w for w in out['widgets'] if w['type'] == 'tasks')['data']
        self.assertEqual(calls['tasks'], [(False, None)])
        self.assertEqual((data['total'], data['in_progress'], data['overdue']), (4, 1, 2))
        tasks = data['tasks']
        self.assertEqual([t['task_id'] for t in tasks], [1, 2, 3])
        self.assertEqual([t['in_progress'] for t in tasks], [True, False, False])
        self.assertEqual([t['overdue'] for t in tasks], [True, False, False])
        self.assertEqual(tasks[0]['teams'], ['Comms'])
        self.assertEqual(tasks[0]['assignee_name'], 'alice')
        self.assertEqual(tasks[1]['teams'], [])
        self.assertIsNone(tasks[2]['due_at'])
        self.assertEqual(tasks[2]['source_case_id'], 10)

    def test_each_tasks_widget_selects_its_tasks(self):
        widgets = [
            {'id': 'w_a', 'type': 'tasks', 'title': None, 'column': 'side',
             'config': {'in_progress_only': True, 'team_ids': [4]}},
            {'id': 'w_b', 'type': 'tasks', 'title': None, 'column': 'main',
             'config': {'in_progress_only': False, 'team_ids': []}},
            {'id': 'w_n', 'type': 'note', 'title': 'Read me', 'column': 'side', 'config': {'text': 'hi'}},
        ]
        out, calls = self._build(attached=[], accessible=[], asset_rows=[], case_rows=[], widgets=widgets)
        self.assertTrue(out['board_customized'])
        self.assertEqual(calls['tasks'], [(True, [4]), (False, None)])
        self.assertEqual([w['id'] for w in out['widgets']], ['w_a', 'w_b', 'w_n'])
        self.assertNotIn('data', out['widgets'][2])
        self.assertEqual(out['widgets'][2]['config'], {'text': 'hi'})

    def test_no_tracker_data_without_tracker_access(self):
        out, calls = self._build(attached=[10], accessible=[10], asset_rows=[], case_rows=[_case_row(10, 'V')])
        self.assertNotIn('trackers', calls)
        self.assertEqual(out['tracker_steps'], [])
        self.assertEqual(out['cases'][0]['highlights'], [])

    def test_tracker_widgets_and_case_highlights(self):
        access = object()
        widgets = [{'id': 'w_p', 'type': 'tracker_progress', 'title': None, 'column': 'main',
                    'config': {'tracker_id': 3}},
                   {'id': 'cases', 'type': 'cases', 'title': None, 'column': 'main',
                    'config': {'highlight_step_ids': [9], 'tracker_ids': None}}]
        data = {'rows_total': 2, 'steps': []}
        step = {'id': 9, 'name': 'Action required'}
        out, calls = self._build(attached=[10, 20], accessible=[10], asset_rows=[],
                                 case_rows=[_case_row(10, 'V'), _case_row(20, 'Secret')], tracker_access=access,
                                 widgets=widgets,
                                 trackers={'data': {'w_p': data}, 'steps': [step],
                                           'case_highlights': {10: [{'step_id': 9, 'count': 2}],
                                                               20: [{'step_id': 9, 'count': 1}]}})
        self.assertEqual(calls['trackers'], (access, widgets))
        self.assertEqual(out['widgets'][0]['data'], data)
        self.assertNotIn('data', out['widgets'][1])
        self.assertEqual(out['tracker_steps'], [step])
        self.assertEqual(out['cases'][0]['highlights'], [{'step_id': 9, 'count': 2}])
        # A case the caller cannot read keeps only its id.
        self.assertEqual(out['cases'][1], {'case_id': 20, 'accessible': False})
