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
"""Unit tests for `iris_alerts_create`: the customer is checked before
anything is written, and only known fields reach the schemas (an id
would load an existing row). The business layer is mocked."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.classification import WRITE_TOOLS
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools import alerts as alerts_tools
from app.models.authorization import Permissions
from app.models.errors import ObjectNotFoundError

_ALERTS = 'app.blueprints.rest.v2.mcp.tools.alerts'


def _query(found):
    query = MagicMock()
    query.filter.return_value.first.return_value = found
    return SimpleNamespace(query=query, severity_id=MagicMock(), status_id=MagicMock(), status_name=MagicMock())


class TestAlertsCreateTool(TestCase):

    def _run(self, payload=None, customer_found=True, customer_access=True, severity=True, status=True, **args):
        self.alert_schema = MagicMock()
        self.alert_schema.load.return_value = SimpleNamespace(alert_id=None)
        self.alert_schema.dump.return_value = {'alert_id': 12}
        self.ioc_schema = MagicMock()
        self.ioc_schema.load.return_value = ['ioc']
        self.asset_schema = MagicMock()
        self.asset_schema.load.return_value = ['asset']
        self.create = MagicMock(return_value=SimpleNamespace(alert_id=12))
        customer = MagicMock(side_effect=None if customer_found else ObjectNotFoundError())
        new_status = SimpleNamespace(status_id=2) if status else None
        with patch(f'{_ALERTS}.customers_get', customer), \
                patch(f'{_ALERTS}.ac_current_user_has_customer_access', return_value=customer_access), \
                patch(f'{_ALERTS}.Severity', _query(SimpleNamespace(severity_id=4) if severity else None)), \
                patch(f'{_ALERTS}.AlertStatus', _query(new_status)), \
                patch(f'{_ALERTS}._alert_schema', self.alert_schema), \
                patch(f'{_ALERTS}._ioc_schema', self.ioc_schema), \
                patch(f'{_ALERTS}._asset_schema', self.asset_schema), \
                patch(f'{_ALERTS}.alerts_create', self.create):
            return alerts_tools.iris_alerts_create({
                'alert_customer_id': 1,
                'payload': {'alert_title': 'Suspicious login', 'alert_severity_id': 4} if payload is None else payload,
                **args})

    def test_alert_should_be_created_for_the_customer_with_the_new_status(self):
        self.assertEqual({'alert_id': 12}, self._run(alert_iocs=[{'ioc_value': '1.2.3.4', 'ioc_type_id': 76,
                                                                   'ioc_tlp_id': 2}],
                                                     alert_assets=[{'asset_name': 'pc', 'asset_type_id': 9}]))
        self.assertEqual({'alert_title': 'Suspicious login', 'alert_severity_id': 4, 'alert_status_id': 2,
                          'alert_customer_id': 1}, self.alert_schema.load.call_args.args[0])
        self.assertEqual(['ioc'], self.create.call_args.args[1])
        self.assertEqual(['asset'], self.create.call_args.args[2])

    def test_customer_without_access_should_be_refused_before_any_write(self):
        for kwargs in ({'customer_found': False}, {'customer_access': False}):
            with self.assertRaises(MCPError, msg=kwargs):
                self._run(**kwargs)
            self.alert_schema.load.assert_not_called()
            self.create.assert_not_called()

    def test_customer_in_the_payload_should_be_refused(self):
        with self.assertRaises(MCPError):
            self._run(payload={'alert_title': 'x', 'alert_severity_id': 4, 'alert_customer_id': 2})
        self.create.assert_not_called()

    def test_ids_in_iocs_or_assets_should_be_refused(self):
        for args in ({'alert_iocs': [{'ioc_id': 3, 'ioc_value': 'x'}]},
                     {'alert_assets': [{'asset_id': 3, 'asset_name': 'x'}]},
                     {'alert_iocs': 'x'}):
            with self.assertRaises(MCPError, msg=args):
                self._run(**args)
        self.ioc_schema.load.assert_not_called()
        self.asset_schema.load.assert_not_called()
        self.create.assert_not_called()

    def test_title_and_known_severity_should_be_required(self):
        for kwargs in ({'payload': {'alert_severity_id': 4}}, {'payload': {'alert_title': 'x'}},
                       {'severity': False}, {'status': False}):
            with self.assertRaises(MCPError, msg=kwargs):
                self._run(**kwargs)
        self.create.assert_not_called()

    def test_tool_should_be_a_write_needing_alerts_write(self):
        spec = TOOL_REGISTRY['iris_alerts_create']
        self.assertIn('iris_alerts_create', WRITE_TOOLS)
        self.assertEqual((Permissions.alerts_write,), tuple(spec.permissions))
        self.assertEqual(['alert_customer_id', 'payload'], spec.input_schema['required'])
