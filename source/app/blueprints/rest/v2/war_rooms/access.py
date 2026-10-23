#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Per-endpoint access checks shared across war-room sub-blueprints."""

from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.access_controls import ac_current_user_permissions_mask
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.access_controls import ac_fast_check_current_user_has_war_room_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.endpoints import response_api_not_found
from app.business.managed_assets import managed_assets_get
from app.business.managed_assets import managed_assets_viewer_scope
from app.business.war_room_scope import war_room_scope_attached_case_ids
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_event_visible
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_gated_case_ids
from app.business.war_room_trackers import WarRoomTrackerAccess
from app.business.war_rooms import war_room_exists
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.authorization import WarRoomAccessLevel
from app.models.errors import ObjectNotFoundError


def require_war_room_read(war_room_id):
    if not war_room_exists(war_room_id):
        return response_api_not_found()
    if not ac_current_user_has_permission(Permissions.war_rooms_read) \
            and not ac_current_user_has_permission(Permissions.server_administrator):
        return ac_api_return_access_denied()
    if ac_fast_check_current_user_has_war_room_access(
        war_room_id,
        [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access],
    ) is None:
        return ac_api_return_access_denied()
    return None


def require_war_room_write(war_room_id):
    if not war_room_exists(war_room_id):
        return response_api_not_found()
    if not ac_current_user_has_permission(Permissions.war_rooms_write) \
            and not ac_current_user_has_permission(Permissions.server_administrator):
        return ac_api_return_access_denied()
    if ac_fast_check_current_user_has_war_room_access(
        war_room_id,
        [WarRoomAccessLevel.full_access],
    ) is None:
        return ac_api_return_access_denied()
    return None


def war_room_redact_decisions(entries):
    """Blank the war-room decision a history entry points to unless the
    caller can read that war room (the finding is readable through its
    case, the decision only through the room)."""
    readable = {}
    for entry in entries:
        war_room_id = entry.get('decision_war_room_id')
        if war_room_id is None:
            continue
        if war_room_id not in readable:
            readable[war_room_id] = require_war_room_read(war_room_id) is None
        if not readable[war_room_id]:
            entry['decision_id'] = None
            entry['decision_number'] = None
            entry['decision_war_room_id'] = None
    return entries


def war_room_redact_flag_history(entries):
    """Blank the war room (and its decision) an asset flag-history entry
    points to unless the caller can read that war room: the history is
    readable through the case, the room only through its own ACL."""
    readable = {}
    for entry in entries:
        war_room_id = entry.get('war_room_id')
        if war_room_id is None:
            continue
        if war_room_id not in readable:
            readable[war_room_id] = require_war_room_read(war_room_id) is None
        if not readable[war_room_id]:
            entry['war_room_id'] = None
            entry['war_room_name'] = None
            entry['decision_id'] = None
            entry['decision_number'] = None
    return entries


def war_room_readable_attached_case_ids(war_room_id):
    """Cases attached to the war room that the caller can read, in
    attachment order (one batched access check)."""
    attached = war_room_scope_attached_case_ids(war_room_id)
    if not attached:
        return []
    granted = ac_fast_check_current_user_has_cases_access(
        attached, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
    )
    return [case_id for case_id in attached if case_id in granted]


def war_room_full_access_case_ids(case_ids):
    """Subset of `case_ids` the caller has full access on (one batched
    check)."""
    case_ids = list(case_ids or ())
    if not case_ids:
        return []
    granted = ac_fast_check_current_user_has_cases_access(case_ids, [CaseAccessLevel.full_access])
    return [case_id for case_id in case_ids if case_id in granted]


def war_room_tracker_access(war_room_id, writing=False):
    """What the caller sees of the room's tracker rows: its readable
    attached cases and, with asset_manager_read, its Assets Manager scope.
    `writing` adds the readable cases it has full access to (where tracker
    timeline events may be written)."""
    registry_scope = None
    if ac_current_user_has_permission(Permissions.asset_manager_read):
        registry_scope = managed_assets_viewer_scope(iris_current_user, ac_current_user_permissions_mask())
    readable = war_room_readable_attached_case_ids(war_room_id)
    writable = war_room_full_access_case_ids(readable) if writing else ()
    return WarRoomTrackerAccess(readable, registry_scope, writable)


class WarRoomEventVisibility:
    """Which war-room timeline events the caller may read. Events written
    by a tracker about a case item are gated by its cases, about a
    registry asset by the caller's Assets Manager scope (see
    `business/war_room_tracker_timelines.py`); the others only by the
    war-room ACL, already checked."""

    def __init__(self, events=()):
        self._readable = set()
        self._checked = set()
        self._assets = {}
        self._scope = False
        self.prime(events)

    def prime(self, events):
        """Check the cases gating `events` in one batch."""
        case_ids = [case_id for case_id in war_room_tracker_timelines_gated_case_ids(events)
                    if case_id not in self._checked]
        if case_ids:
            granted = ac_fast_check_current_user_has_cases_access(
                case_ids, [CaseAccessLevel.read_only, CaseAccessLevel.full_access])
            self._readable.update(case_id for case_id in case_ids if case_id in granted)
            self._checked.update(case_ids)

    def _asset_visible(self, managed_asset_id):
        if managed_asset_id not in self._assets:
            visible = False
            if ac_current_user_has_permission(Permissions.asset_manager_read):
                if self._scope is False:
                    self._scope = managed_assets_viewer_scope(iris_current_user, ac_current_user_permissions_mask())
                try:
                    managed_assets_get(managed_asset_id, self._scope)
                    visible = True
                except ObjectNotFoundError:
                    visible = False
            self._assets[managed_asset_id] = visible
        return self._assets[managed_asset_id]

    def visible(self, event):
        self.prime([event])
        return war_room_tracker_timelines_event_visible(event, self._readable, self._asset_visible)

    def filter(self, events):
        events = list(events)
        self.prime(events)
        return [event for event in events if self.visible(event)]
