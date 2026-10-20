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

"""`get_alerts_observables_summary`: the first few observables per alert.

`_first_observables` runs for real on the two association tables, in an
in-memory SQLite database; the assets and IOCs it points at are loaded
through a patched session.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.datamgmt.alerts import alerts_db
from app.datamgmt.alerts.alerts_db import _alert_list_options
from app.datamgmt.alerts.alerts_db import _first_observables
from app.datamgmt.alerts.alerts_db import get_alerts_observables_summary
from app.models.assets import alert_assets_association


class TestFirstObservables(TestCase):

    def setUp(self):
        engine = create_engine('sqlite://')
        alert_assets_association.create(engine)
        self.session = Session(engine)
        rows = [{'alert_id': 1, 'asset_id': asset_id} for asset_id in range(500, 0, -1)]
        rows += [{'alert_id': 2, 'asset_id': 7}, {'alert_id': 3, 'asset_id': 7}]
        self.session.execute(insert(alert_assets_association), rows)
        self.db = patch.object(alerts_db, 'db', SimpleNamespace(session=self.session))
        self.db.start()

    def tearDown(self):
        self.db.stop()
        self.session.close()

    def _first(self, alert_ids, limit):
        return _first_observables(alert_assets_association, alert_assets_association.c.asset_id, alert_ids, limit)

    def test_should_keep_the_first_ids_and_count_them_all(self):
        self.assertEqual({1: ([1, 2, 3], 500), 2: ([7], 1)}, self._first([1, 2], 3))

    def test_should_leave_out_alerts_without_observables(self):
        self.assertEqual({}, self._first([42], 10))


class TestGetAlertsObservablesSummary(TestCase):

    def test_should_return_an_entry_per_alert(self):
        asset = SimpleNamespace(asset_id=5)
        ioc = SimpleNamespace(ioc_id=9)
        session = MagicMock()
        session.query.return_value.options.return_value.filter.return_value.all.side_effect = [[asset], [ioc]]

        def first(association, _column, _alert_ids, _limit):
            if association is alert_assets_association:
                return {1: ([5], 500)}
            return {1: ([9], 2)}

        with patch.object(alerts_db, 'db', SimpleNamespace(session=session)), \
                patch.object(alerts_db, '_first_observables', side_effect=first):
            summary = get_alerts_observables_summary([1, 2, 1])

        self.assertEqual({
            1: {'assets': [asset], 'assets_count': 500, 'iocs': [ioc], 'iocs_count': 2},
            2: {'assets': [], 'assets_count': 0, 'iocs': [], 'iocs_count': 0}
        }, summary)

    def test_should_not_query_without_alerts(self):
        with patch.object(alerts_db, '_first_observables') as first:
            self.assertEqual({}, get_alerts_observables_summary([]))
        first.assert_not_called()


class TestAlertListOptions(TestCase):

    def test_should_leave_the_observables_out_on_request(self):
        def loaded(options):
            return {str(option.path) for option in options}

        full = loaded(_alert_list_options())
        without = loaded(_alert_list_options(include_observables=False))
        self.assertTrue(without < full)
        self.assertFalse([path for path in without if 'assets' in path or 'iocs' in path])
        self.assertTrue([path for path in full if 'assets' in path])
