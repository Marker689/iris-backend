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

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.alerts import alerts_load_for_display


class TestAlertsLoadForDisplay(TestCase):

    def test_should_return_the_alert_reloaded_with_its_relationships(self):
        alert = SimpleNamespace(alert_id=12)
        reloaded = SimpleNamespace(alert_id=12)
        with patch('app.business.alerts.get_alert_for_display', return_value=reloaded) as load:
            self.assertIs(reloaded, alerts_load_for_display(alert))
        load.assert_called_once_with(12)

    def test_should_keep_the_alert_when_it_is_gone_from_the_database(self):
        alert = SimpleNamespace(alert_id=12)
        with patch('app.business.alerts.get_alert_for_display', return_value=None):
            self.assertIs(alert, alerts_load_for_display(alert))

    def test_should_pass_through_what_is_not_an_alert(self):
        with patch('app.business.alerts.get_alert_for_display') as load:
            self.assertIsNone(alerts_load_for_display(None))
            self.assertEqual({'k': 1}, alerts_load_for_display({'k': 1}))
        load.assert_not_called()
