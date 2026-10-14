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

"""Unit tests for the timeline event write tools: the payload the model
writes is completed into the one `EventSchema` reads. The business layer
and the schema are mocked."""

from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.tools import events as events_tools


_EVENTS = 'app.blueprints.rest.v2.mcp.tools.events'


class _EventToolTest(TestCase):

    def setUp(self):
        self.event = SimpleNamespace(event_id=5, case_id=4, event_title='Logon', event_tz='+02:00',
                                     event_date=datetime(2026, 10, 14, 6, 30),
                                     event_date_wtz=datetime(2026, 10, 14, 8, 30))
        self.schema = MagicMock()
        self.schema.load.return_value = self.event
        self.schema.validate_date.return_value = (None, None)
        self.schema.get_event_category_id.return_value = 3
        self.schema.dump.return_value = {'event_id': 5}
        for target, value in (('_event_schema', self.schema),
                              ('events_get', MagicMock(return_value=self.event)),
                              ('events_get_assets_ids', MagicMock(return_value=[1])),
                              ('events_get_iocs_ids', MagicMock(return_value=[2]))):
            patcher = patch(f'{_EVENTS}.{target}', value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _loaded_payload(self):
        return self.schema.load.call_args.args[0]


class TestEventsCreateTool(_EventToolTest):

    def _create(self, payload):
        with patch(f'{_EVENTS}.events_create', return_value=self.event) as create:
            events_tools.iris_case_events_create({'case_identifier': 4, 'payload': payload})
        return create.call_args

    def test_date_is_put_in_the_schema_format_and_its_offset_used_as_timezone(self):
        self._create({'event_title': 'Logon', 'event_category_id': 3, 'event_date': '2026-10-14T08:30:00+02:00'})
        loaded = self._loaded_payload()
        self.assertEqual('2026-10-14T08:30:00.000000', loaded['event_date'])
        self.assertEqual('+02:00', loaded['event_tz'])

    def test_links_and_timezone_default_when_omitted(self):
        call = self._create({'event_title': 'Logon', 'event_category_id': 3, 'event_date': '2026-10-14T08:30:00'})
        self.assertEqual('+00:00', self._loaded_payload()['event_tz'])
        self.assertEqual((4, self.event, 3, [], [], False), call.args)

    def test_unreadable_date_is_refused_before_the_load(self):
        with self.assertRaises(MCPError):
            self._create({'event_title': 'Logon', 'event_category_id': 3, 'event_date': 'yesterday'})
        self.schema.load.assert_not_called()


class TestEventsUpdateTool(_EventToolTest):

    def _update(self, payload, case_identifier=4):
        with patch(f'{_EVENTS}.events_update', return_value=self.event) as update:
            events_tools.iris_case_events_update(
                {'case_identifier': case_identifier, 'event_identifier': 5, 'payload': payload})
        return update.call_args

    def test_partial_payload_keeps_the_current_values(self):
        call = self._update({'event_content': 'confirmed'})
        loaded = self._loaded_payload()
        self.assertEqual({'event_title': 'Logon', 'event_category_id': 3, 'event_assets': [1], 'event_iocs': [2],
                          'event_date': '2026-10-14T08:30:00.000000', 'event_tz': '+02:00',
                          'event_content': 'confirmed', 'event_id': 5}, loaded)
        self.assertEqual((self.event, 3, [1], [2], False), call.args)

    def test_given_links_replace_the_current_ones(self):
        call = self._update({'event_assets': [], 'event_iocs': [7]})
        self.assertEqual(([], [7]), call.args[2:4])

    def test_new_date_without_offset_keeps_the_current_timezone(self):
        self._update({'event_date': '2026-10-15T09:00:00'})
        self.assertEqual('+02:00', self._loaded_payload()['event_tz'])

    def test_new_date_with_offset_brings_its_timezone(self):
        self._update({'event_date': '2026-10-15T09:00:00-05:00'})
        self.assertEqual('-05:00', self._loaded_payload()['event_tz'])

    def test_event_of_another_case_is_refused(self):
        with self.assertRaises(MCPError):
            self._update({'event_title': 'Moved'}, case_identifier=9)
        self.schema.load.assert_not_called()
