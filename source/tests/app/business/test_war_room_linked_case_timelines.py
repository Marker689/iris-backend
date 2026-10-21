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

"""The linked-case timelines projection and the war-room event hydration
query in batches: their query count must not grow with the number of
attached cases or events. The models are mocked, no DB needed."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_linked_case_timelines as linked
from app.business import war_room_timelines


def _rows(*rows):
    return [SimpleNamespace(**row) for row in rows]


class TestAccessibleLinkedCaseIds(TestCase):

    def test_checks_every_linked_case_in_one_batch(self):
        with patch.object(linked, 'WarRoomCase') as war_room_case, \
                patch.object(linked, 'ac_fast_check_user_has_cases_access') as access:
            war_room_case.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
                {'case_id': 1}, {'case_id': 2}, {'case_id': 3})
            access.return_value = {1: 4, 3: 4}

            result = linked._accessible_linked_case_ids(9, 5)

        self.assertEqual({1, 3}, result)
        access.assert_called_once()
        self.assertEqual([1, 2, 3], access.call_args.args[1])

    def test_returns_nothing_without_linked_case(self):
        with patch.object(linked, 'WarRoomCase') as war_room_case, \
                patch.object(linked, 'ac_fast_check_user_has_cases_access') as access:
            war_room_case.query.filter.return_value.with_entities.return_value.all.return_value = []

            self.assertEqual(set(), linked._accessible_linked_case_ids(9, 5))

        access.assert_not_called()


class TestListLinkedCaseTimelines(TestCase):

    def test_groups_the_timelines_of_every_case_with_one_query(self):
        timelines = [
            SimpleNamespace(case_id=2, timeline_id=20, name='Main', color=None, is_default=True),
            SimpleNamespace(case_id=1, timeline_id=10, name='Main', color='#ff0000', is_default=True),
            SimpleNamespace(case_id=2, timeline_id=21, name='Lateral', color=None, is_default=False),
        ]
        with patch.object(linked, '_accessible_linked_case_ids', return_value={1, 2, 3}), \
                patch.object(linked, 'WarRoomCase') as war_room_case, \
                patch.object(linked, 'Cases') as cases, \
                patch.object(linked, 'CaseTimeline') as case_timeline:
            (war_room_case.query.filter.return_value.order_by.return_value
             .with_entities.return_value.all.return_value) = _rows(
                {'case_id': 2, 'attached_at': None}, {'case_id': 1, 'attached_at': None},
                {'case_id': 3, 'attached_at': None})
            cases.query.filter.return_value.with_entities.return_value.all.return_value = [
                (1, '#1 - One'), (2, '#2 - Two')]
            case_timeline.query.filter.return_value.order_by.return_value.all.return_value = timelines

            result = linked.list_linked_case_timelines(9, 5)

        case_timeline.query.filter.assert_called_once()
        self.assertEqual([2, 1, 3], [c['case_id'] for c in result])
        self.assertEqual([20, 21], [t['timeline_id'] for t in result[0]['timelines']])
        self.assertEqual([10], [t['timeline_id'] for t in result[1]['timelines']])
        self.assertEqual([], result[2]['timelines'])
        self.assertEqual('Case #3', result[2]['case_name'])


class TestCaseEventsHydration(TestCase):

    def test_groups_assets_iocs_and_children_per_event(self):
        assets = MagicMock()
        assets.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
            {'event_id': 1, 'asset_id': 11}, {'event_id': 1, 'asset_id': 12})
        iocs = MagicMock()
        iocs.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
            {'event_id': 2, 'ioc_id': 21})
        models = MagicMock(CaseEventsAssets=assets, CaseEventsIoc=iocs)
        with patch.dict('sys.modules', {'app.models.models': models}), \
                patch.object(linked, 'CasesEvent') as cases_event:
            cases_event.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
                {'parent_event_id': 2}, {'parent_event_id': 2})

            result = linked._case_events_hydration([1, 2, 1])

        self.assertEqual({1: ([11, 12], [], 0), 2: ([], [21], 2)}, result)

    def test_returns_nothing_without_event(self):
        self.assertEqual({}, linked._case_events_hydration([]))


class TestWarRoomEventsHydration(TestCase):

    def test_groups_assets_iocs_and_children_per_event(self):
        with patch.object(war_room_timelines, 'WarRoomTimelineEventAsset') as assets, \
                patch.object(war_room_timelines, 'WarRoomTimelineEventIoc') as iocs, \
                patch.object(war_room_timelines, 'WarRoomTimelineEvent') as events:
            assets.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
                {'event_id': 3, 'asset_id': 31})
            iocs.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
                {'event_id': 3, 'ioc_id': 32}, {'event_id': 4, 'ioc_id': 41})
            events.query.filter.return_value.with_entities.return_value.all.return_value = _rows(
                {'parent_id': 3})

            result = war_room_timelines.events_hydration([3, 4])

        self.assertEqual({3: ([31], [32], 1), 4: ([], [41], 0)}, result)

    def test_returns_nothing_without_event(self):
        self.assertEqual({}, war_room_timelines.events_hydration([]))
