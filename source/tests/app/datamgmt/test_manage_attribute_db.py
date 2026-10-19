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

"""Unit tests for merging submitted custom attribute values (iris-web#1154).

The models and the session are patched out; what is under test is the
shape of the attributes the merge leaves on the object.
"""

import copy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.datamgmt.manage.manage_attribute_db import merge_custom_attributes
from app.datamgmt.manage.manage_attribute_db import normalize_object_attributes

_MODULE = 'app.datamgmt.manage.manage_attribute_db'


def _field(type_='input_string', value=''):
    return {'type': type_, 'mandatory': False, 'value': value}


_SCHEMA = {
    'SLA': {'Status': _field(), 'Deadline': _field()},
    'Report': {'Content': _field('html', '<b>schema</b>')},
}


class TestNormalizeObjectAttributes(TestCase):

    def test_none_becomes_the_schema(self):
        self.assertEqual(_SCHEMA, normalize_object_attributes(None, _SCHEMA))

    def test_bare_legacy_values_are_rebuilt_from_the_schema(self):
        result = normalize_object_attributes({'SLA': {'Status': 'Breached'}}, _SCHEMA)
        self.assertEqual(_field(value='Breached'), result['SLA']['Status'])
        self.assertEqual(_field(), result['SLA']['Deadline'])

    def test_bare_values_outside_the_schema_become_strings(self):
        result = normalize_object_attributes({'Old': {'Field': 'x'}}, _SCHEMA)
        self.assertEqual(_field(value='x'), result['Old']['Field'])

    def test_module_tabs_and_well_formed_values_are_kept(self):
        stored = {'VT Report': {'HTML report': _field('html', '<b>12/70</b>')},
                  'SLA': {'Status': _field('input_datetime', '2026-10-19T10:00')}}
        result = normalize_object_attributes(stored, _SCHEMA)
        self.assertEqual(stored['VT Report'], result['VT Report'])
        self.assertEqual(stored['SLA']['Status'], result['SLA']['Status'])

    def test_inputs_are_not_mutated(self):
        stored = {'SLA': {'Status': 'x'}}
        schema = copy.deepcopy(_SCHEMA)
        normalize_object_attributes(stored, schema)
        self.assertEqual({'SLA': {'Status': 'x'}}, stored)
        self.assertEqual(_SCHEMA, schema)


class TestMergeCustomAttributes(TestCase):

    def _merge(self, stored, data, obj_id=42):
        case = SimpleNamespace(case_id=obj_id, custom_attributes=stored)
        schema_row = SimpleNamespace(attribute_content=copy.deepcopy(_SCHEMA))
        cases = MagicMock()
        cases.query.filter.return_value.first.return_value = case
        attributes = MagicMock()
        attributes.query.filter.return_value.first.return_value = schema_row
        with patch(f'{_MODULE}.Cases', cases), \
                patch(f'{_MODULE}.CustomAttribute', attributes), \
                patch(f'{_MODULE}.flag_modified'), \
                patch(f'{_MODULE}.db'):
            result = merge_custom_attributes(data, obj_id, 'case')
        self.assertEqual(_SCHEMA, schema_row.attribute_content, 'the schema must not be mutated')
        return case, result

    def test_object_without_attributes_takes_the_submitted_value(self):
        case, result = self._merge(None, {'SLA': {'Status': 'On track'}})
        self.assertEqual(_field(value='On track'), result['SLA']['Status'])
        self.assertIs(result, case.custom_attributes)

    def test_object_with_bare_legacy_values_can_be_saved(self):
        _, result = self._merge({'SLA': {'Status': 'old', 'Deadline': 'x'}}, {'SLA': {'Status': 'new'}})
        self.assertEqual(_field(value='new'), result['SLA']['Status'])
        self.assertEqual(_field(value='x'), result['SLA']['Deadline'])

    def test_schema_field_missing_on_the_object_is_not_dropped(self):
        _, result = self._merge({'SLA': {'Status': _field(value='a')}}, {'SLA': {'Deadline': 'tomorrow'}})
        self.assertEqual(_field(value='tomorrow'), result['SLA']['Deadline'])

    def test_tabs_not_submitted_are_kept(self):
        stored = {'VT Report': {'HTML report': _field('html', '<b>12/70</b>')}}
        _, result = self._merge(stored, {'SLA': {'Status': 'x'}})
        self.assertEqual(stored['VT Report'], result['VT Report'])

    def test_html_is_not_writable_by_the_client(self):
        _, result = self._merge(None, {'Report': {'Content': '<script>alert(1)</script>'}})
        self.assertEqual('<b>schema</b>', result['Report']['Content']['value'])

    def test_stored_shape_echoed_by_a_client_is_unwrapped(self):
        _, result = self._merge(None, {'SLA': {'Status': _field(value='echo')}})
        self.assertEqual('echo', result['SLA']['Status']['value'])

    def test_unknown_tabs_and_fields_are_ignored(self):
        _, result = self._merge(None, {'Nope': {'x': 1}, 'SLA': {'Nope': 1}})
        self.assertNotIn('Nope', result)
        self.assertNotIn('Nope', result['SLA'])

    def test_creation_fills_the_schema_without_mutating_it(self):
        attributes = MagicMock()
        schema_row = SimpleNamespace(attribute_content=copy.deepcopy(_SCHEMA))
        attributes.query.filter.return_value.first.return_value = schema_row
        with patch(f'{_MODULE}.CustomAttribute', attributes):
            result = merge_custom_attributes({'SLA': {'Status': 'new'}}, None, 'case')
        self.assertEqual(_field(value='new'), result['SLA']['Status'])
        self.assertEqual(_SCHEMA, schema_row.attribute_content)
