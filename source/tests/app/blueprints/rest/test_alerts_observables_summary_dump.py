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

"""`?observables=summary` on the alert listings: the dump side.

The alerts are transient `Alert` objects and the summary query is
patched out, so neither a database nor a Flask context is needed.
"""

from unittest import TestCase
from unittest.mock import patch

from app.blueprints.rest.v2.alerts import _AlertsObservablesSummaryDump
from app.models.alerts import Alert
from app.schema.marshables import AlertSchema


def _summary(*alert_ids, assets_count=0, iocs_count=0):
    return {alert_id: {'assets': [], 'assets_count': assets_count, 'iocs': [], 'iocs_count': iocs_count}
            for alert_id in alert_ids}


class TestAlertsObservablesSummaryDump(TestCase):

    def test_should_dump_what_the_full_schema_does_plus_the_counts(self):
        alert = Alert(alert_id=3, alert_title='brute force')
        with patch('app.blueprints.rest.v2.alerts.alerts_observables_summary',
                   return_value=_summary(3, assets_count=500, iocs_count=2)):
            dumped = _AlertsObservablesSummaryDump(None).dump([alert], many=True)

        full = AlertSchema().dump(alert)
        self.assertEqual(set(full) | {'assets_count', 'iocs_count'}, set(dumped[0]))
        self.assertEqual(500, dumped[0]['assets_count'])
        self.assertEqual(2, dumped[0]['iocs_count'])

    def test_should_query_the_summary_once_for_the_whole_page(self):
        alerts = [Alert(alert_id=1), Alert(alert_id=2), Alert(alert_id=1)]
        with patch('app.blueprints.rest.v2.alerts.alerts_observables_summary',
                   return_value=_summary(1, 2)) as summary:
            dumped = _AlertsObservablesSummaryDump(None).dump(alerts, many=True)

        summary.assert_called_once_with([1, 2, 1])
        self.assertEqual([1, 2, 1], [data['alert_id'] for data in dumped])

    def test_should_dump_a_single_alert(self):
        with patch('app.blueprints.rest.v2.alerts.alerts_observables_summary',
                   return_value=_summary(7, iocs_count=1)):
            dumped = _AlertsObservablesSummaryDump(None).dump(Alert(alert_id=7))
        self.assertEqual(7, dumped['alert_id'])
        self.assertEqual(1, dumped['iocs_count'])

    def test_should_only_add_the_observables_fields_names(self):
        with patch('app.blueprints.rest.v2.alerts.alerts_observables_summary',
                   return_value=_summary(3, assets_count=4, iocs_count=2)):
            dumped = _AlertsObservablesSummaryDump(['alert_id', 'iocs']).dump(Alert(alert_id=3))
        self.assertEqual({'alert_id': 3, 'iocs': [], 'iocs_count': 2}, dumped)

    def test_should_not_query_the_summary_when_fields_names_no_observables(self):
        with patch('app.blueprints.rest.v2.alerts.alerts_observables_summary') as summary:
            dumped = _AlertsObservablesSummaryDump(['alert_id']).dump(Alert(alert_id=3))
        summary.assert_not_called()
        self.assertEqual({'alert_id': 3}, dumped)
