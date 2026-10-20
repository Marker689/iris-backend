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

"""`page_alert_group_units` must page exactly like the query it replaced.

The grouped queue used to sort the union of every unit and count it. The
replacement only sorts the rows that can reach the requested page and
derives the unit total, which is what makes it cheap — and also what could
make it subtly wrong. So both are run side by side on the same data, an
in-memory SQLite database with the two columns the queries touch, across
both sort directions, every page and a few page sizes.
"""

import random
from unittest import TestCase

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import asc
from sqlalchemy import create_engine
from sqlalchemy import desc
from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import literal
from sqlalchemy.orm import Session
from sqlalchemy.orm import declarative_base

from app.datamgmt.alerts.alerts_db import page_alert_group_units

_Base = declarative_base()

# Cluster ids live in their own range so a cluster and an orphan never tie
# on both the sort value and the unit id: that order is unspecified in the
# old query and the new one alike, and comparing it would test nothing.
_FIRST_CLUSTER_ID = 10_000


class _Alert(_Base):
    __tablename__ = 'test_group_page_alert'

    alert_id = Column(Integer, primary_key=True)
    sort_value = Column(Integer, nullable=True)


class _Association(_Base):
    __tablename__ = 'test_group_page_association'

    alert_id = Column(Integer, primary_key=True)
    cluster_id = Column(Integer, primary_key=True)


def _reference_page(session, matches, association, ascending, page, per_page):
    """The query `page_alert_group_units` replaced, kept verbatim in spirit."""
    order_func = asc if ascending else desc
    cluster_value = func.min if ascending else func.max

    cluster_units = session.query(
        literal('cluster').label('kind'),
        association.cluster_id.label('unit_id'),
        cluster_value(matches.c.sort_value).label('sort_value')
    ).join(
        matches, matches.c.alert_id == association.alert_id
    ).group_by(association.cluster_id)

    orphan_units = session.query(
        literal('alert').label('kind'),
        matches.c.alert_id.label('unit_id'),
        matches.c.sort_value.label('sort_value')
    ).filter(
        ~exists().where(association.alert_id == matches.c.alert_id)
    )

    units = cluster_units.union_all(orphan_units).subquery()

    total = session.query(func.count()).select_from(units).scalar() or 0
    total_alerts = session.query(func.count()).select_from(matches).scalar() or 0
    rows = session.query(
        units.c.kind, units.c.unit_id
    ).order_by(
        order_func(units.c.sort_value), order_func(units.c.unit_id)
    ).limit(per_page).offset((page - 1) * per_page).all()

    return [(kind, unit_id) for kind, unit_id in rows], total, total_alerts


class TestPageAlertGroupUnits(TestCase):

    def setUp(self):
        self._engine = create_engine('sqlite://')
        _Base.metadata.create_all(self._engine)
        self._session = Session(self._engine)

    def tearDown(self):
        self._session.close()
        self._engine.dispose()

    def _populate(self, seed, alert_count, cluster_count, null_ratio=0.0):
        generator = random.Random(seed)
        for alert_id in range(1, alert_count + 1):
            # A narrow value range so ties are common: the tiebreaker is
            # where paging bugs hide.
            value = None if generator.random() < null_ratio else generator.randint(0, alert_count // 3 + 1)
            self._session.add(_Alert(alert_id=alert_id, sort_value=value))
        for cluster_id in range(_FIRST_CLUSTER_ID, _FIRST_CLUSTER_ID + cluster_count):
            members = generator.sample(range(1, alert_count + 1), generator.randint(1, 5))
            for alert_id in members:
                self._session.merge(_Association(alert_id=alert_id, cluster_id=cluster_id))
        self._session.commit()

    def _matches(self, max_alert_id=None):
        query = self._session.query(
            _Alert.alert_id.label('alert_id'),
            _Alert.sort_value.label('sort_value')
        )
        if max_alert_id is not None:
            query = query.filter(_Alert.alert_id <= max_alert_id)
        return query.subquery()

    def _assert_same_pages(self, matches):
        for ascending in (True, False):
            for per_page in (1, 3, 7, 25):
                expected_first = _reference_page(self._session, matches, _Association, ascending, 1, per_page)
                last_page = max(1, -(-expected_first[1] // per_page))
                # One page past the end too: it must come back empty with
                # the same totals.
                for page in range(1, last_page + 2):
                    expected = _reference_page(self._session, matches, _Association, ascending, page, per_page)
                    actual = page_alert_group_units(self._session, matches, _Association, ascending, page, per_page)
                    self.assertEqual(
                        expected, actual,
                        f'ascending={ascending} per_page={per_page} page={page}'
                    )

    def test_matches_the_reference_query_on_mostly_orphan_data(self):
        # The shape that made the old query slow: a few clusters among
        # many alerts.
        self._populate(seed=1, alert_count=120, cluster_count=4)

        self._assert_same_pages(self._matches())

    def test_matches_the_reference_query_on_heavily_clustered_data(self):
        self._populate(seed=2, alert_count=60, cluster_count=30)

        self._assert_same_pages(self._matches())

    def test_matches_the_reference_query_with_null_sort_values(self):
        # Sorting by owner or status can produce NULLs; they must land in
        # the same place whichever half of the merge they come from.
        self._populate(seed=3, alert_count=80, cluster_count=10, null_ratio=0.2)

        self._assert_same_pages(self._matches())

    def test_matches_the_reference_query_when_filters_split_a_cluster(self):
        # A filter that keeps only some members of a cluster: the cluster
        # must be ranked on its matching members alone, and clusters with
        # no matching member must disappear from the total.
        self._populate(seed=4, alert_count=90, cluster_count=15)

        self._assert_same_pages(self._matches(max_alert_id=40))

    def test_empty_result(self):
        actual = page_alert_group_units(self._session, self._matches(), _Association, False, 1, 25)

        self.assertEqual(([], 0, 0), actual)

    def test_counts_units_and_alerts_separately(self):
        # Alerts 1-3 in cluster A, alert 3 also in cluster B, 4-5 orphans:
        # 2 clusters + 2 orphans = 4 units, over 5 matching alerts.
        for alert_id in range(1, 6):
            self._session.add(_Alert(alert_id=alert_id, sort_value=alert_id))
        for alert_id, cluster_id in ((1, 10_000), (2, 10_000), (3, 10_000), (3, 10_001)):
            self._session.add(_Association(alert_id=alert_id, cluster_id=cluster_id))
        self._session.commit()

        rows, total_units, total_alerts = page_alert_group_units(
            self._session, self._matches(), _Association, False, 1, 25
        )

        self.assertEqual(4, total_units)
        self.assertEqual(5, total_alerts)
        self.assertEqual([('alert', 5), ('alert', 4), ('cluster', 10_001), ('cluster', 10_000)], rows)
