#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the OIDC group mapping (OIDC_MAPPING_USERGROUP / OIDC_MAPPING_ROLES).

The callback reads the group claim, possibly nested (`realm_access.roles`),
turns the names into IRIS group ids and syncs the account to exactly those.
`get_groups_list` is stubbed: what is under test is which ids a token yields.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from oic.oic.message import IdToken

from app.blueprints.pages.login.login_routes import _oidc_claim
from app.blueprints.pages.login.login_routes import _oidc_claim_groups
from app.blueprints.pages.login.login_routes import _oidc_group_ids
from app.configuration import IrisConfigException
from app.configuration import _parse_oidc_roles_mapping

_GROUPS = [
    SimpleNamespace(group_id=1, group_name='Administrators'),
    SimpleNamespace(group_id=2, group_name='Analysts'),
    SimpleNamespace(group_id=3, group_name='Readers'),
]


class TestClaimPath(TestCase):

    def test_top_level_claim(self):
        self.assertEqual(['a'], _oidc_claim({'groups': ['a']}, 'groups'))

    def test_nested_claim(self):
        claims = {'realm_access': {'roles': ['iris-analysts']}}
        self.assertEqual(['iris-analysts'], _oidc_claim(claims, 'realm_access.roles'))

    def test_nested_under_a_dotted_key(self):
        """Keycloak client roles sit under the client id, which may hold dots."""
        claims = {'resource_access': {'iris.prod': {'roles': ['reader']}}}
        self.assertEqual(['reader'], _oidc_claim(claims, 'resource_access.iris.prod.roles'))

    def test_dotted_top_level_key_wins(self):
        """Namespaced claims are URLs: the whole key matches before any split."""
        claims = {'https://example.com/groups': ['a'], 'https://example': {'com/groups': ['b']}}
        self.assertEqual(['a'], _oidc_claim(claims, 'https://example.com/groups'))

    def test_missing_claim_is_none(self):
        self.assertIsNone(_oidc_claim({'realm_access': {}}, 'realm_access.roles'))
        self.assertIsNone(_oidc_claim({'realm_access': ['x']}, 'realm_access.roles'))
        self.assertIsNone(_oidc_claim({}, 'groups'))
        self.assertIsNone(_oidc_claim({'groups': ['a']}, ''))

    def test_reads_a_pyoidc_token(self):
        token = IdToken(sub='s', realm_access={'roles': ['iris-analysts']}, preferred_username='alice')
        self.assertEqual(['iris-analysts'], _oidc_claim(token, 'realm_access.roles'))
        self.assertEqual('alice', _oidc_claim(token, 'preferred_username'))


class TestClaimGroups(TestCase):

    def test_list_is_kept(self):
        self.assertEqual(['a', 'b'], _oidc_claim_groups({'groups': ['a', 'b']}, 'groups'))

    def test_single_string_is_one_group(self):
        self.assertEqual(['Analysts'], _oidc_claim_groups({'groups': 'Analysts'}, 'groups'))

    def test_absent_claim_is_none(self):
        self.assertIsNone(_oidc_claim_groups({}, 'groups'))

    def test_empty_list_stays_empty(self):
        self.assertEqual([], _oidc_claim_groups({'groups': []}, 'groups'))

    def test_unexpected_shape_is_no_group(self):
        self.assertEqual([], _oidc_claim_groups({'groups': {'Analysts': True}}, 'groups'))

    def test_non_name_entries_are_dropped(self):
        self.assertEqual(['a', '7'], _oidc_claim_groups({'groups': ['a', None, '', 7, {'x': 1}]}, 'groups'))


class TestGroupIds(TestCase):

    def _ids(self, names, mapping=None):
        with patch('app.blueprints.pages.login.login_routes.get_groups_list', return_value=_GROUPS):
            return _oidc_group_ids(names, mapping)

    def test_names_match_iris_groups(self):
        self.assertEqual({2}, self._ids(['Analysts', 'Unknown']))

    def test_name_match_is_exact(self):
        """Keycloak's "Full group path" sends `/Analysts`, which is not `Analysts`."""
        self.assertEqual(set(), self._ids(['/Analysts', 'analysts']))

    def test_mapping_replaces_name_matching(self):
        self.assertEqual({2}, self._ids(['iris-analysts', 'Readers'], {'iris-analysts': 2}))

    def test_mapping_to_a_deleted_group_is_skipped(self):
        self.assertEqual({3}, self._ids(['gone', 'readers'], {'gone': 99, 'readers': 3}))

    def test_no_names_is_no_group(self):
        self.assertEqual(set(), self._ids([]))


class TestRolesMappingConfig(TestCase):

    def test_ids_are_normalised_to_int(self):
        self.assertEqual({'a': 2, 'b': 3}, _parse_oidc_roles_mapping('{"a": 2, "b": "3"}'))

    def test_invalid_json_is_refused(self):
        with self.assertRaises(IrisConfigException):
            _parse_oidc_roles_mapping("{'a': 2}")

    def test_non_object_is_refused(self):
        with self.assertRaises(IrisConfigException):
            _parse_oidc_roles_mapping('[2, 3]')

    def test_non_id_value_is_refused(self):
        for raw in ('{"a": [1, 2]}', '{"a": "admins"}', '{"a": true}', '{"a": -1}'):
            with self.subTest(raw=raw), self.assertRaises(IrisConfigException):
                _parse_oidc_roles_mapping(raw)
