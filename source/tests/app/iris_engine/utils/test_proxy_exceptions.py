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

"""The "No proxy for" list of the server settings: what it matches, and
what the settings refuse to save."""

from unittest import TestCase

from app.iris_engine.utils.proxy_exceptions import PROXY_BYPASS
from app.iris_engine.utils.proxy_exceptions import proxy_exceptions_errors
from app.iris_engine.utils.proxy_exceptions import proxy_exceptions_match
from app.iris_engine.utils.proxy_exceptions import proxy_exceptions_request_proxies
from app.schema.marshables import ServerSettingsSchema


class TestsProxyExceptionsMatch(TestCase):

    def assertMatches(self, rules, *urls):
        for url in urls:
            self.assertTrue(proxy_exceptions_match(url, rules), f'{rules!r} should match {url}')

    def assertNotMatches(self, rules, *urls):
        for url in urls:
            self.assertFalse(proxy_exceptions_match(url, rules), f'{rules!r} should not match {url}')

    def test_nothing_should_match_an_empty_list(self):
        for rules in (None, '', ' ,\n'):
            self.assertNotMatches(rules, 'https://intranet.corp/')

    def test_star_should_match_every_host(self):
        self.assertMatches('*', 'https://www.virustotal.com/', 'http://10.0.0.1:8080/x')

    def test_domain_should_match_itself_and_its_subdomains(self):
        self.assertMatches('corp.example', 'https://corp.example/', 'https://jira.corp.example/a',
                           'http://a.b.corp.example:8080/')
        self.assertNotMatches('corp.example', 'https://evilcorp.example/', 'https://corp.example.org/')

    def test_leading_dot_or_star_should_match_the_subdomains_only(self):
        for rules in ('.corp.example', '*.corp.example'):
            self.assertMatches(rules, 'https://jira.corp.example/')
            self.assertNotMatches(rules, 'https://corp.example/', 'https://evilcorp.example/')

    def test_match_should_ignore_case_and_a_trailing_dot(self):
        self.assertMatches('Jira.Corp.Example.', 'https://JIRA.corp.example./')

    def test_ip_address_should_match_that_address_only(self):
        self.assertMatches('10.1.2.3', 'http://10.1.2.3/', 'https://10.1.2.3:8443/')
        self.assertNotMatches('10.1.2.3', 'http://10.1.2.30/')
        self.assertMatches('fd00::1', 'http://[fd00::1]/')

    def test_network_should_match_addresses_written_in_the_url_only(self):
        self.assertMatches('10.0.0.0/8', 'http://10.20.30.40/')
        self.assertMatches('fd00::/8', 'https://[fd12::5]:8443/')
        self.assertNotMatches('10.0.0.0/8', 'http://11.0.0.1/', 'https://intranet.corp/', 'http://[fd00::1]/')

    def test_local_should_match_host_names_without_a_dot(self):
        self.assertMatches('<local>', 'http://jira/', 'http://localhost:8080/')
        self.assertNotMatches('<local>', 'http://jira.corp/', 'http://127.0.0.1/', 'http://[::1]/')

    def test_port_should_restrict_the_match(self):
        self.assertMatches('jira.corp:8443', 'https://jira.corp:8443/')
        self.assertNotMatches('jira.corp:8443', 'https://jira.corp/', 'http://jira.corp:8080/')
        self.assertMatches('jira.corp:443', 'https://jira.corp/')
        self.assertMatches('[fd00::1]:8443', 'https://[fd00::1]:8443/')
        self.assertNotMatches('[fd00::1]:8443', 'https://[fd00::1]/')

    def test_any_of_several_entries_should_match(self):
        rules = 'localhost, 127.0.0.1\n.corp.example; 192.168.0.0/16'
        self.assertMatches(rules, 'http://localhost/', 'https://a.corp.example/', 'http://192.168.1.1/')
        self.assertNotMatches(rules, 'https://www.virustotal.com/')

    def test_invalid_entries_should_be_skipped(self):
        self.assertMatches('http://bad, jira.corp', 'https://jira.corp/')

    def test_request_proxies_should_bypass_on_match_only(self):
        self.assertEqual(PROXY_BYPASS, proxy_exceptions_request_proxies('https://jira.corp/', 'jira.corp'))
        self.assertIsNone(proxy_exceptions_request_proxies('https://www.virustotal.com/', 'jira.corp'))


class TestsProxyExceptionsValidation(TestCase):

    def test_valid_entries_should_pass(self):
        rules = ('*, <local>, localhost, jira.corp, .corp, *.corp, jira.corp:8443, 10.1.2.3, 10.1.2.3:80, '
                 '10.0.0.0/8, fd00::1, [fd00::1]:8443, fd00::/8, my_host')
        self.assertEqual([], proxy_exceptions_errors(rules))

    def test_invalid_entries_should_be_listed(self):
        self.assertEqual(['http://jira.corp', 'jira.corp:0', 'bad/host', '-a.corp', '[fd00::1'],
                         proxy_exceptions_errors('http://jira.corp jira.corp:0 bad/host ok.corp -a.corp [fd00::1'))

    def test_server_settings_should_refuse_invalid_entries(self):
        schema = ServerSettingsSchema()
        self.assertEqual({}, schema.validate({'no_proxy': '.corp, 10.0.0.0/8'}, partial=True))
        self.assertEqual({}, schema.validate({'no_proxy': None}, partial=True))
        errors = schema.validate({'no_proxy': 'jira.corp, http://x'}, partial=True)
        self.assertIn('http://x', str(errors['no_proxy']))
