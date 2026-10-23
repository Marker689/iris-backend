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

"""Who sees and touches the war-room timeline events written by trackers:
case-gated events need one of their cases, registry-asset events the
asset, and case timelines are only written with full access.

No database: access helpers and business functions are patched."""

import inspect
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2.war_rooms import access
from app.blueprints.rest.v2.war_rooms import timelines as timeline_routes
from app.blueprints.rest.v2.war_rooms import trackers as tracker_routes
from app.models.authorization import CaseAccessLevel
from app.models.errors import ObjectNotFoundError


_ACCESS = 'app.blueprints.rest.v2.war_rooms.access'
_TIMELINES = 'app.blueprints.rest.v2.war_rooms.timelines'
_TRACKERS = 'app.blueprints.rest.v2.war_rooms.trackers'


def _event(event_id, case_ids=None, managed_asset_id=None):
    return SimpleNamespace(id=event_id, visibility_case_ids=case_ids, visibility_managed_asset_id=managed_asset_id)


class TestFullAccessCaseIds(TestCase):

    def test_one_batched_full_access_check(self):
        with patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access', return_value=[3]) as check:
            self.assertEqual(access.war_room_full_access_case_ids([1, 3]), [3])
        check.assert_called_once_with([1, 3], [CaseAccessLevel.full_access])

    def test_nothing_to_check(self):
        with patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access') as check:
            self.assertEqual(access.war_room_full_access_case_ids(None), [])
            self.assertEqual(access.war_room_full_access_case_ids([]), [])
        check.assert_not_called()


class TestEventVisibility(TestCase):

    def setUp(self):
        self.stack = ExitStack()
        self.cases = self.stack.enter_context(
            patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access',
                  side_effect=lambda case_ids, _levels: [c for c in case_ids if c in (1, 3)]))
        self.permission = self.stack.enter_context(
            patch(f'{_ACCESS}.ac_current_user_has_permission', return_value=True))
        self.stack.enter_context(patch(f'{_ACCESS}.ac_current_user_permissions_mask', return_value=0))
        self.stack.enter_context(patch(f'{_ACCESS}.iris_current_user', SimpleNamespace(id=7)))
        self.scope = self.stack.enter_context(patch(f'{_ACCESS}.managed_assets_viewer_scope',
                                                    return_value='scope'))

        def _get(asset_id, scope):
            if asset_id != 5 or scope != 'scope':
                raise ObjectNotFoundError()
            return {}
        self.asset = self.stack.enter_context(patch(f'{_ACCESS}.managed_assets_get', side_effect=_get))

    def tearDown(self):
        self.stack.close()

    def test_events_are_filtered_with_one_case_check(self):
        events = [_event(1), _event(2, [1]), _event(3, [2]), _event(4, [2, 3]), _event(5, [])]
        out = access.WarRoomEventVisibility().filter(events)
        self.assertEqual([e.id for e in out], [1, 2, 4])
        self.cases.assert_called_once_with([1, 2, 3], [CaseAccessLevel.read_only, CaseAccessLevel.full_access])

    def test_cases_already_checked_are_not_checked_again(self):
        visibility = access.WarRoomEventVisibility([_event(1, [1, 2])])
        self.assertFalse(visibility.visible(_event(2, [2])))
        self.assertTrue(visibility.visible(_event(3, [3])))
        self.assertEqual([call.args[0] for call in self.cases.call_args_list], [[1, 2], [3]])

    def test_registry_asset_events_need_the_asset_in_the_scope(self):
        visibility = access.WarRoomEventVisibility()
        out = visibility.filter([_event(1, managed_asset_id=5), _event(2, managed_asset_id=6),
                                 _event(3, managed_asset_id=5)])
        self.assertEqual([e.id for e in out], [1, 3])
        self.scope.assert_called_once()
        self.assertEqual([call.args[0] for call in self.asset.call_args_list], [5, 6])
        self.cases.assert_not_called()

    def test_registry_asset_events_need_asset_manager_read(self):
        self.permission.return_value = False
        self.assertEqual(access.WarRoomEventVisibility().filter([_event(1, managed_asset_id=5)]), [])
        self.asset.assert_not_called()


class _Route(TestCase):

    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(app.test_request_context('/', method='POST', json={}))
        self.stack.enter_context(patch(f'{_TIMELINES}.require_war_room_read', return_value=None))
        self.stack.enter_context(patch(f'{_TIMELINES}.require_war_room_write', return_value=None))
        self.stack.enter_context(patch(f'{_TIMELINES}.iris_current_user', SimpleNamespace(id=7)))
        self.stack.enter_context(patch(f'{_TIMELINES}.response_api_not_found', return_value='404'))
        self.stack.enter_context(patch(f'{_TIMELINES}.response_api_success', side_effect=lambda data: data))
        self.stack.enter_context(patch(f'{_TIMELINES}.response_api_deleted', return_value='deleted'))
        self.events = {1: _event(1), 2: _event(2, [9])}

        def _get(_war_room_id, event_id):
            if event_id not in self.events:
                raise ObjectNotFoundError()
            return self.events[event_id]
        self.stack.enter_context(patch(f'{_TIMELINES}.get_timeline_event', side_effect=_get))
        self.stack.enter_context(patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access', return_value=[]))

    def tearDown(self):
        self.stack.close()


class TestTimelineRoutes(_Route):

    def test_the_list_hides_the_events_of_unreadable_cases(self):
        self.stack.enter_context(patch(f'{_TIMELINES}.list_timeline_events',
                                       return_value=list(self.events.values())))
        self.stack.enter_context(patch(f'{_TIMELINES}.events_hydration',
                                       side_effect=lambda ids: {i: None for i in ids}))
        self.stack.enter_context(patch(f'{_TIMELINES}._serialize_event', side_effect=lambda e, _h=None: e.id))
        self.assertEqual(inspect.unwrap(timeline_routes.list_events)(10), [1])

    def test_a_hidden_event_cannot_be_touched(self):
        mutations = {name: self.stack.enter_context(patch(f'{_TIMELINES}.{name}'))
                     for name in ('delete_timeline_event', 'toggle_event_flag', 'duplicate_event',
                                  'set_event_assets', 'set_event_iocs', 'update_timeline_event')}
        for route in (timeline_routes.patch_event, timeline_routes.remove_event, timeline_routes.flag_event,
                      timeline_routes.duplicate_event_route, timeline_routes.replace_event_assets,
                      timeline_routes.replace_event_iocs):
            for event_id in (2, 3):
                with self.subTest(route=route.__name__, event_id=event_id):
                    self.assertEqual(inspect.unwrap(route)(10, event_id), '404')
        for mutation in mutations.values():
            mutation.assert_not_called()

    def test_a_visible_event_is_deleted(self):
        delete = self.stack.enter_context(patch(f'{_TIMELINES}.delete_timeline_event'))
        self.assertEqual(inspect.unwrap(timeline_routes.remove_event)(10, 1), 'deleted')
        delete.assert_called_once_with(10, 1)

    def test_a_hidden_parent_cannot_be_chosen(self):
        create = self.stack.enter_context(patch(f'{_TIMELINES}.create_timeline_event'))
        with app.test_request_context('/', method='POST', json={'title': 'x', 'parent_id': 2}):
            self.assertEqual(inspect.unwrap(timeline_routes.add_event)(10, 4), '404')
        create.assert_not_called()


class TestTrackerRoutes(TestCase):

    def _access(self, writing):
        with patch(f'{_ACCESS}.ac_current_user_has_permission', return_value=False), \
                patch(f'{_ACCESS}.war_room_readable_attached_case_ids', return_value=[1, 2]), \
                patch(f'{_ACCESS}.ac_fast_check_current_user_has_cases_access', return_value=[2]) as check:
            return access.war_room_tracker_access(10, writing=writing), check

    def test_writing_adds_the_full_access_cases(self):
        result, check = self._access(True)
        self.assertEqual(result.writable_case_ids, {2})
        check.assert_called_once_with([1, 2], [CaseAccessLevel.full_access])

    def test_reading_writes_nowhere(self):
        result, check = self._access(False)
        self.assertEqual(result.writable_case_ids, set())
        check.assert_not_called()

    def test_sync_needs_war_room_write(self):
        sync = MagicMock()
        with app.test_request_context('/', method='POST'), \
                patch(f'{_TRACKERS}.require_war_room_write', return_value='403'), \
                patch(f'{_TRACKERS}.war_room_trackers_timeline_sync', sync):
            self.assertEqual(inspect.unwrap(tracker_routes.sync_tracker_timeline)(10, 4), '403')
        sync.assert_not_called()

    def test_sync_passes_the_writable_cases(self):
        tracker = SimpleNamespace(id=4, name='T')
        summary = {'events': 1, 'skipped_case_ids': [1], 'restricted_rows': 0, 'war_room_rows': 0}
        with app.test_request_context('/', method='POST'), \
                patch(f'{_TRACKERS}.require_war_room_write', return_value=None), \
                patch(f'{_TRACKERS}.war_room_trackers_get', return_value=tracker), \
                patch(f'{_TRACKERS}.war_room_tracker_access', return_value='access') as build_access, \
                patch(f'{_TRACKERS}._emit_tracker_event') as emit, \
                patch(f'{_TRACKERS}.iris_current_user', SimpleNamespace(id=7)), \
                patch(f'{_TRACKERS}.response_api_success', side_effect=lambda data: data), \
                patch(f'{_TRACKERS}.war_room_trackers_timeline_sync', return_value=summary) as sync:
            self.assertEqual(inspect.unwrap(tracker_routes.sync_tracker_timeline)(10, 4), summary)
        build_access.assert_called_once_with(10, writing=True)
        sync.assert_called_once_with(10, tracker, 'access', 7)
        emit.assert_called_once()
