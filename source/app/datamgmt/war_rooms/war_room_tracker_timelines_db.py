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

"""Persistence of the timeline events written by war-room trackers. Every
write is flushed, never committed: the caller commits with the tracker
change."""

from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.cases import CaseEventTimeline
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.war_rooms import WarRoomTimeline
from app.models.war_rooms import WarRoomTimelineEvent
from app.models.war_rooms import WarRoomTrackerRowTimelineEvent


def war_room_tracker_timelines_db_add(obj):
    """Add and flush (the id is needed right away)."""
    db.session.add(obj)
    db.session.flush()
    return obj


def _get_or_create(query, build):
    """The row of `query`, created with `build()` when missing. A
    concurrent creation (unique name) is caught in a savepoint and the
    winner returned. Returns `(row, created)`."""
    row = query().first()
    if row is not None:
        return row, False
    try:
        with db.session.begin_nested():
            row = build()
            db.session.add(row)
    except IntegrityError:
        return query().first(), False
    return row, True


def war_room_tracker_timelines_db_case_timeline(case_id, name, build):
    """The case timeline named `name`, created with `build()` when missing.
    Returns `(timeline, created)`."""
    return _get_or_create(
        lambda: CaseTimeline.query.filter(CaseTimeline.case_id == case_id, CaseTimeline.name == name), build)


def war_room_tracker_timelines_db_war_room_timeline_by_name(war_room_id, name, build):
    """The war-room timeline named `name`, created with `build()` when
    missing. Returns `(timeline, created)`."""
    return _get_or_create(
        lambda: WarRoomTimeline.query.filter(WarRoomTimeline.war_room_id == war_room_id,
                                             WarRoomTimeline.name == name), build)


def war_room_tracker_timelines_db_war_room_timeline(war_room_id, timeline_id):
    return WarRoomTimeline.query.filter(WarRoomTimeline.war_room_id == war_room_id,
                                        WarRoomTimeline.timeline_id == timeline_id).first()


def war_room_tracker_timelines_db_link_case_event(event_id, case_id, timeline_id, asset_ids, ioc_ids):
    db.session.add(CaseEventTimeline(event_id=event_id, timeline_id=timeline_id))
    for asset_id in asset_ids:
        db.session.add(CaseEventsAssets(event_id=event_id, asset_id=asset_id, case_id=case_id))
    for ioc_id in ioc_ids:
        db.session.add(CaseEventsIoc(event_id=event_id, ioc_id=ioc_id, case_id=case_id))
    db.session.flush()


def war_room_tracker_timelines_db_case_event(case_id, event_id):
    return CasesEvent.query.filter(CasesEvent.case_id == case_id, CasesEvent.event_id == event_id).first()


def war_room_tracker_timelines_db_war_room_event(war_room_id, event_id):
    return (
        WarRoomTimelineEvent.query
        .join(WarRoomTimeline, WarRoomTimeline.timeline_id == WarRoomTimelineEvent.timeline_id)
        .filter(WarRoomTimeline.war_room_id == war_room_id, WarRoomTimelineEvent.id == event_id)
        .first()
    )


def war_room_tracker_timelines_db_state_links(row_ids):
    """The state events of the rows, as `{row_id: [link]}`."""
    if not row_ids:
        return {}
    out = {}
    links = WarRoomTrackerRowTimelineEvent.query.filter(
        WarRoomTrackerRowTimelineEvent.row_id.in_(list(row_ids))).all()
    for link in links:
        out.setdefault(link.row_id, []).append(link)
    return out
