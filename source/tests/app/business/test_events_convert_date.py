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

from unittest import TestCase

from app.business.events import events_convert_date
from app.models.errors import BusinessProcessingError


class TestEventsConvertDate(TestCase):

    def test_iso_datetime_without_timezone_defaults_to_utc(self):
        result = events_convert_date('2024-03-08 03:02:01')
        self.assertEqual({'date': '2024-03-08', 'time': '03:02:01.000', 'tz': '+00:00'}, result)

    def test_iso_datetime_keeps_milliseconds(self):
        result = events_convert_date('2024-03-08T03:02:01.123456')
        self.assertEqual('03:02:01.123', result['time'])

    def test_timezone_offset_is_formatted_with_colon(self):
        result = events_convert_date('2024-03-08 03:02:01+0200')
        self.assertEqual('+02:00', result['tz'])
        self.assertEqual('03:02:01.000', result['time'])

    def test_negative_timezone_offset(self):
        result = events_convert_date('2024-03-08T03:02:01-0530')
        self.assertEqual('-05:30', result['tz'])

    def test_unix_timestamp_is_read_as_utc(self):
        result = events_convert_date('1700000000')
        self.assertEqual({'date': '2023-11-14', 'time': '22:13:20.000', 'tz': '+00:00'}, result)

    def test_millisecond_timestamp_is_read_as_utc(self):
        result = events_convert_date('1700000000123')
        self.assertEqual({'date': '2023-11-14', 'time': '22:13:20.123', 'tz': '+00:00'}, result)

    def test_surrounding_whitespace_is_ignored(self):
        result = events_convert_date('  08/03/2024 03:02:01  ')
        self.assertEqual('2024-03-08', result['date'])

    def test_unparseable_value_raises(self):
        with self.assertRaises(BusinessProcessingError):
            events_convert_date('not a date')

    def test_empty_value_raises(self):
        with self.assertRaises(BusinessProcessingError):
            events_convert_date('   ')
