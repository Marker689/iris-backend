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

"""The 2 MiB cap on AI workflow definition bodies. No DB, no HTTP: an
oversized upload against the live stack stalls the REST suite."""

from io import BytesIO
from unittest import TestCase

from app import app
from app.blueprints.rest.v2 import ai_workflows

_CAP = 2 * 1024 * 1024


def _body(size):
    return b'{"name": "w"}'.ljust(size, b' ')


class TestsAiWorkflowsBodyCap(TestCase):

    def _definition_body(self, data, chunked=False):
        if chunked:
            context = app.test_request_context('/', method='POST', input_stream=BytesIO(data),
                                               headers={'Transfer-Encoding': 'chunked'},
                                               environ_base={'wsgi.input_terminated': True})
        else:
            context = app.test_request_context('/', method='POST', data=data)
        with context:
            return ai_workflows._definition_body()

    def test_body_under_the_cap_should_be_parsed(self):
        body, refused = self._definition_body(_body(_CAP))
        self.assertIsNone(refused)
        self.assertEqual({'name': 'w'}, body)

    def test_body_over_the_cap_should_return_413_and_close_the_connection(self):
        body, refused = self._definition_body(_body(_CAP + 1))
        self.assertIsNone(body)
        self.assertEqual(413, refused.status_code)
        self.assertEqual('close', refused.headers['Connection'])

    def test_chunked_body_over_the_cap_should_return_413(self):
        body, refused = self._definition_body(_body(_CAP + 1), chunked=True)
        self.assertIsNone(body)
        self.assertEqual(413, refused.status_code)

    def test_oversized_body_should_be_drained(self):
        with app.test_request_context('/', method='POST', data=_body(_CAP + 1)) as context:
            ai_workflows._definition_body()
            self.assertEqual(b'', context.request.stream.read())
