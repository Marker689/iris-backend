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

"""`iris_customers_get` reads a customer, its contacts and its
key/values, by identifier (customer access) or through a case (case
access); and the key/values of `CustomerSchema` are validated. The
business layer is patched. No DB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.classification import is_read_only
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools.customers import iris_customers_get
from app.blueprints.rest.v2.mcp.tools.customers import iris_customers_list
from app.models.authorization import Permissions
from app.models.customers import Client
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import CustomerSchema

_TOOLS = 'app.blueprints.rest.v2.mcp.tools.customers'

_CASE_IDENTIFIER = 11
_CUSTOMER_IDENTIFIER = 3
_TENANT = '0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b'


class TestsCustomersGetRegistration(TestCase):

    def test_tool_should_be_a_read_needing_customers_read(self):
        self.assertTrue(is_read_only('iris_customers_get'))
        self.assertEqual((Permissions.customers_read,), TOOL_REGISTRY['iris_customers_get'].permissions)


class TestsCustomersGet(TestCase):

    def setUp(self):
        self.customer = Client(client_id=_CUSTOMER_IDENTIFIER, name='Contoso', description='d', sla='4h',
                               key_values={'ENTRA_TENANT_ID': _TENANT})
        self.case_access = True
        self.customer_access = True
        patch(f'{_TOOLS}.ac_fast_check_current_user_has_case_access',
              side_effect=lambda _identifier, _levels: self.case_access).start()
        self.has_customer_access = patch(f'{_TOOLS}.ac_current_user_has_customer_access',
                                         side_effect=lambda _identifier: self.customer_access).start()
        self.get_case = patch(f'{_TOOLS}.cases_get_by_identifier',
                              return_value=SimpleNamespace(case_id=_CASE_IDENTIFIER,
                                                           client_id=_CUSTOMER_IDENTIFIER)).start()
        self.get_customer = patch(f'{_TOOLS}.customers_get', side_effect=lambda _identifier: self.customer).start()
        patch(f'{_TOOLS}.customers_contacts_list', return_value=[]).start()
        context = app.test_request_context('/')
        context.push()
        self.addCleanup(context.pop)
        self.addCleanup(patch.stopall)

    def assertRefused(self, args, code, message):
        with self.assertRaises(MCPError) as raised:
            iris_customers_get(args)
        self.assertEqual(code, raised.exception.code)
        self.assertIn(message, raised.exception.message)

    def test_customer_should_be_read_with_its_key_values(self):
        result = iris_customers_get({'customer_id': _CUSTOMER_IDENTIFIER})
        self.assertEqual((_CUSTOMER_IDENTIFIER, 'Contoso', {'ENTRA_TENANT_ID': _TENANT}, []),
                         (result['customer_id'], result['customer_name'], result['customer_key_values'],
                          result['contacts']))
        self.get_customer.assert_called_once_with(_CUSTOMER_IDENTIFIER)

    def test_customer_without_key_values_should_have_an_empty_mapping(self):
        self.customer.key_values = None
        self.assertEqual({}, iris_customers_get({'customer_id': _CUSTOMER_IDENTIFIER})['customer_key_values'])

    def test_customer_of_a_case_should_be_read_with_case_access_only(self):
        self.customer_access = False
        result = iris_customers_get({'case_identifier': _CASE_IDENTIFIER})
        self.assertEqual(_CUSTOMER_IDENTIFIER, result['customer_id'])
        self.has_customer_access.assert_not_called()

    def test_case_and_its_own_customer_should_be_accepted(self):
        result = iris_customers_get({'case_identifier': _CASE_IDENTIFIER, 'customer_id': _CUSTOMER_IDENTIFIER})
        self.assertEqual(_CUSTOMER_IDENTIFIER, result['customer_id'])

    def test_case_of_another_customer_should_be_refused(self):
        self.assertRefused({'case_identifier': _CASE_IDENTIFIER, 'customer_id': 4}, protocol.INVALID_PARAMS,
                           'does not belong to customer #4')
        self.get_customer.assert_not_called()

    def test_inaccessible_case_should_be_denied(self):
        self.case_access = False
        self.assertRefused({'case_identifier': _CASE_IDENTIFIER}, protocol.IRIS_ACCESS_DENIED,
                           f'No access to case #{_CASE_IDENTIFIER}')
        self.get_case.assert_not_called()

    def test_missing_case_should_be_denied_like_a_forbidden_one(self):
        self.get_case.side_effect = ObjectNotFoundError()
        self.assertRefused({'case_identifier': 999}, protocol.IRIS_ACCESS_DENIED, 'No access to case #999')

    def test_inaccessible_customer_should_be_denied(self):
        self.customer_access = False
        self.assertRefused({'customer_id': _CUSTOMER_IDENTIFIER}, protocol.IRIS_ACCESS_DENIED,
                           f'Not entitled to customer #{_CUSTOMER_IDENTIFIER}')
        self.get_customer.assert_not_called()

    def test_missing_customer_should_be_refused(self):
        self.get_customer.side_effect = ObjectNotFoundError()
        self.assertRefused({'customer_id': 999}, protocol.INVALID_PARAMS, 'Customer not found')

    def test_no_argument_should_be_refused(self):
        self.assertRefused({}, protocol.INVALID_PARAMS, 'Give customer_id or case_identifier')


class TestsCustomerSchemaKeyValues(TestCase):

    def _errors(self, key_values):
        with app.app_context():
            return CustomerSchema().validate({'customer_key_values': key_values}, partial=True)

    def test_key_values_should_be_accepted(self):
        self.assertEqual({}, self._errors({'ENTRA_TENANT_ID': _TENANT, 'siem.index': 'contoso-*', 'a-b_c': ''}))
        self.assertEqual({}, self._errors(None))

    def test_bad_keys_should_be_refused(self):
        for key in ('', 'with space', 'k' * 65, 'é', 'KEY\n'):
            with self.subTest(key=key):
                self.assertIn('customer_key_values', self._errors({key: 'v'}))

    def test_values_should_be_strings_of_at_most_4096_characters(self):
        self.assertEqual({}, self._errors({'K': 'v' * 4096}))
        self.assertIn('customer_key_values', self._errors({'K': 'v' * 4097}))
        self.assertIn('customer_key_values', self._errors({'K': 3}))

    def test_at_most_100_entries_should_be_accepted(self):
        self.assertEqual({}, self._errors({f'K{n}': 'v' for n in range(100)}))
        self.assertIn('customer_key_values', self._errors({f'K{n}': 'v' for n in range(101)}))

    def test_key_values_should_be_dumped_and_loaded_under_their_api_name(self):
        customer = Client(client_id=1, name='Contoso', key_values={'K': 'v'})
        with app.app_context():
            dumped = CustomerSchema().dump(customer)
        self.assertEqual({'K': 'v'}, dumped['customer_key_values'])
        self.assertNotIn('key_values', dumped)


class TestsCustomersList(TestCase):

    def setUp(self):
        self.page = SimpleNamespace(page=1, has_next=False, items=[
            Client(client_id=_CUSTOMER_IDENTIFIER, name='Contoso', description='d', key_values={'WT': 'wt-77'}),
            Client(client_id=4, name='ACME Corp', description=None, key_values=None),
        ])
        self.is_admin = False
        self.filter = patch(f'{_TOOLS}.customers_filter', side_effect=lambda *_args, **_kwargs: self.page).start()
        patch(f'{_TOOLS}.ac_current_user_has_permission', side_effect=lambda _permission: self.is_admin).start()
        context = app.test_request_context('/')
        context.push()
        self.addCleanup(context.pop)
        self.addCleanup(patch.stopall)

    def test_tool_should_be_a_read_needing_customers_read(self):
        self.assertTrue(is_read_only('iris_customers_list'))
        self.assertEqual((Permissions.customers_read,), TOOL_REGISTRY['iris_customers_list'].permissions)

    def test_customers_should_be_listed_with_their_key_values(self):
        result = iris_customers_list({})
        self.assertEqual([(_CUSTOMER_IDENTIFIER, 'Contoso', {'WT': 'wt-77'}), (4, 'ACME Corp', {})],
                         [(c['customer_id'], c['customer_name'], c['customer_key_values'])
                          for c in result['customers']])
        self.assertEqual((1, False), (result['page'], result['has_more']))

    def test_filters_should_reach_the_business_layer(self):
        self.is_admin = True
        iris_customers_list({'search': '  acme ', 'customer_ids': [4], 'page': 2})
        _user, pagination, is_admin = self.filter.call_args.args
        self.assertEqual((2, 100, True), (pagination.get_page(), pagination.get_per_page(), is_admin))
        self.assertEqual({'search': 'acme', 'identifiers': [4]}, self.filter.call_args.kwargs)

    def test_blank_search_should_not_filter(self):
        iris_customers_list({'search': '   '})
        self.assertEqual({'search': None, 'identifiers': None}, self.filter.call_args.kwargs)
