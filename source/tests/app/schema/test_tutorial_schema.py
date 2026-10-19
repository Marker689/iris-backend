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

import unittest

from marshmallow import ValidationError

from app.schema.marshables import TutorialSchema
from app.schema.marshables import TutorialStepSchema


def _step(**overrides):
    step = {'title': 'Open the cases', 'body': 'Click **Cases**.'}
    step.update(overrides)
    return step


class TestTutorialSchema(unittest.TestCase):

    def setUp(self):
        self.schema = TutorialSchema()

    def _load(self, **overrides):
        data = {'title': 'Our triage routine', 'steps': [_step()]}
        data.update(overrides)
        return self.schema.load(data)

    def test_minimal_payload_loads_with_defaults(self):
        tutorial = self._load()
        self.assertEqual('', tutorial.summary)
        self.assertEqual('', tutorial.duration)
        self.assertFalse(tutorial.published)
        self.assertIsNone(tutorial.builtin_id)

    def test_builtin_id_loads(self):
        self.assertEqual('first-investigation', self._load(builtin_id='first-investigation').builtin_id)

    def test_builtin_id_that_is_not_a_tutorial_id_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self._load(builtin_id='First Investigation')
        self.assertIn('builtin_id', ctx.exception.messages)

    def test_missing_title_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'steps': []})
        self.assertIn('title', ctx.exception.messages)

    def test_draft_may_have_no_steps(self):
        self.assertEqual([], self._load(steps=[]).steps)

    def test_published_without_steps_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self._load(steps=[], published=True)
        self.assertIn('steps', ctx.exception.messages)

    def test_too_many_steps_raises(self):
        with self.assertRaises(ValidationError):
            self._load(steps=[_step()] * 101)

    def test_steps_round_trip_in_camel_case(self):
        step = _step(
            anchor=['case-form', 'case-new'],
            goTo='/case/{caseId}/iocs',
            fills=[{'anchor': 'case-name', 'label': 'Name', 'value': 'Phishing'},
                   {'anchor': 'vuln-private', 'label': 'Private', 'value': True}],
            waitFor={'kind': 'api', 'method': 'POST', 'path': '/cases',
                     'capture': {'caseId': 'case_id'}},
        )
        tutorial = self._load(steps=[step])
        self.assertIn('go_to', tutorial.steps[0])
        dumped = self.schema.dump(tutorial)
        self.assertEqual(step, dumped['steps'][0])


class TestTutorialStepSchema(unittest.TestCase):

    def setUp(self):
        self.schema = TutorialStepSchema()

    def test_blank_optionals_are_dropped(self):
        loaded = self.schema.load(_step(anchor='', goTo=None, fills=[], waitFor=None))
        self.assertEqual({'title', 'body'}, set(loaded))

    def test_empty_body_is_allowed(self):
        self.assertEqual('', self.schema.load(_step(body=''))['body'])

    def test_go_to_must_stay_inside_the_app(self):
        for target in ('https://evil.example', '//evil.example', 'javascript:alert(1)',
                       '/\\evil.example'):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                self.schema.load(_step(goTo=target))

    def test_anchor_accepts_a_string_or_a_short_list(self):
        self.assertEqual('a[href="/cases"]', self.schema.load(_step(anchor='a[href="/cases"]'))['anchor'])
        with self.assertRaises(ValidationError):
            self.schema.load(_step(anchor=['a'] * 6))
        with self.assertRaises(ValidationError):
            self.schema.load(_step(anchor=[1]))

    def test_fill_value_must_be_text_or_boolean(self):
        fill = {'anchor': 'case-name', 'label': 'Name', 'value': 12}
        with self.assertRaises(ValidationError):
            self.schema.load(_step(fills=[fill]))

    def test_api_wait_needs_method_and_path(self):
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor={'kind': 'api', 'path': '/cases'}))
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor={'kind': 'api', 'method': 'POST'}))

    def test_api_wait_refuses_get(self):
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor={'kind': 'api', 'method': 'GET', 'path': '/cases'}))

    def test_capture_names_must_be_identifiers(self):
        wait = {'kind': 'api', 'method': 'POST', 'path': '/cases', 'capture': {'case id': 'case_id'}}
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor=wait))

    def test_route_and_element_waits_need_their_target(self):
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor={'kind': 'route'}))
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor={'kind': 'element'}))

    def test_wait_keeps_only_the_fields_of_its_kind(self):
        wait = {'kind': 'route', 'path': '/alerts', 'method': 'POST', 'anchor': 'x'}
        self.assertEqual({'kind': 'route', 'path': '/alerts'},
                         self.schema.load(_step(waitFor=wait))['wait_for'])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValidationError):
            self.schema.load(_step(waitFor={'kind': 'click', 'anchor': 'x'}))
