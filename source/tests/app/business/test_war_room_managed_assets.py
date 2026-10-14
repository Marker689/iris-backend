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

"""Unit tests for the registry assets attached to a war room's scope."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business import war_room_managed_assets as biz
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BIZ = 'app.business.war_room_managed_assets'


def _vulnerability(vulnerability_id=7, identifier='CVE-2024-3400'):
    return SimpleNamespace(vulnerability_id=vulnerability_id, identifier=identifier, is_private=False, kind='cve',
                           title='PAN-OS', cvss_score=10.0, cvss_version='3.1', severity='critical',
                           epss_score=None, kev=True, exploit_maturity='in-the-wild', patch_availability='patch')


def _row(attachment_id=1, managed_asset_id=30, case_id=None, vulnerability=None):
    attachment = SimpleNamespace(id=attachment_id, managed_asset_id=managed_asset_id, case_id=case_id,
                                 vulnerability_id=vulnerability.vulnerability_id if vulnerability else None,
                                 note='edge', added_at=datetime.datetime(2026, 10, 14, 9, 0), added_by_id=3)
    asset = SimpleNamespace(managed_asset_id=managed_asset_id, name='fw-01', asset_type_id=2, ip='10.0.0.1',
                            domain=None, description=None, tags=None, criticality='high', environment='production',
                            owner='netops', is_active=True, client_id=1)
    return SimpleNamespace(WarRoomManagedAsset=attachment, ManagedAsset=asset, asset_type_name='Firewall',
                           customer_name='Acme', case_name='Case 1' if case_id else None,
                           Vulnerability=vulnerability, added_by_name='Ana')


def _tag(managed_asset_id=30):
    return SimpleNamespace(managed_asset_id=managed_asset_id, finding_id=5, vulnerability_id=7,
                           identifier='CVE-2024-3400', severity='critical', kev=True,
                           remediation_status='affected', exploitation_status='exploited')


class TestList(TestCase):

    def test_list_should_serialize_links_and_registry_findings(self):
        rows = [_row(case_id=4, vulnerability=_vulnerability())]
        with patch(f'{_BIZ}.war_room_managed_assets_db_list', return_value=rows) as db_list, \
                patch('app.business.vulnerability_findings.findings_db_managed_asset_tags', return_value=[_tag()]):
            out = biz.war_room_managed_assets_list(9, 'scope', [4], True)
        db_list.assert_called_once_with(9, 'scope', [4], biz.WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT)
        item = out['data'][0]
        self.assertEqual(item['asset_name'], 'fw-01')
        self.assertEqual(item['case_id'], 4)
        self.assertEqual(item['case_name'], 'Case 1')
        self.assertEqual(item['vulnerability']['identifier'], 'CVE-2024-3400')
        self.assertEqual(item['vulnerabilities'][0]['finding_id'], 5)
        self.assertEqual(item['added_at'], '2026-10-14T09:00:00')
        self.assertFalse(out['truncated'])

    def test_list_should_drop_vulnerability_data_without_the_permission(self):
        rows = [_row(vulnerability=_vulnerability())]
        with patch(f'{_BIZ}.war_room_managed_assets_db_list', return_value=rows), \
                patch('app.business.vulnerability_findings.findings_db_managed_asset_tags') as tags:
            out = biz.war_room_managed_assets_list(9, 'scope', [], False)
        tags.assert_not_called()
        self.assertNotIn('vulnerability', out['data'][0])
        self.assertNotIn('vulnerabilities', out['data'][0])

    def test_list_should_flag_truncation(self):
        rows = [_row(attachment_id=i) for i in range(3)]
        with patch(f'{_BIZ}.WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT', 2), \
                patch(f'{_BIZ}.war_room_managed_assets_db_list', return_value=rows), \
                patch('app.business.vulnerability_findings.findings_db_managed_asset_tags', return_value=[]):
            out = biz.war_room_managed_assets_list(9, 'scope', [], True)
        self.assertTrue(out['truncated'])
        self.assertEqual(len(out['data']), 2)


class TestAttach(TestCase):

    def setUp(self):
        self.mocks = {}
        patches = {
            'get': patch(f'{_BIZ}.managed_assets_get'),
            'find': patch(f'{_BIZ}.war_room_managed_assets_db_find', return_value=None),
            'count': patch(f'{_BIZ}.war_room_managed_assets_db_count', return_value=0),
            'add': patch(f'{_BIZ}.war_room_managed_assets_db_add'),
            'commit': patch(f'{_BIZ}.war_room_managed_assets_db_commit', return_value=True),
            'vuln': patch(f'{_BIZ}.vulnerabilities_db_get', return_value=_vulnerability()),
            'track': patch(f'{_BIZ}.track_activity'),
        }
        for name, patcher in patches.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def _attach(self, body, readable=(4,), can_link=True):
        return biz.war_room_managed_assets_attach(9, body, 3, 'scope', list(readable), can_link)

    def test_attach_without_links_should_create_unlinked_rows(self):
        out = self._attach({'managed_asset_ids': [30, 31, 30]})
        self.assertEqual(out, {'created': [30, 31], 'existing': [], 'not_found': []})
        added = [call.args[0] for call in self.mocks['add'].call_args_list]
        self.assertEqual([(a.managed_asset_id, a.case_id, a.vulnerability_id) for a in added],
                         [(30, None, None), (31, None, None)])
        self.mocks['track'].assert_called_once()

    def test_attach_should_link_case_and_vulnerability(self):
        self._attach({'managed_asset_ids': [30], 'case_id': 4, 'vulnerability_id': 7, 'note': ' edge '})
        added = self.mocks['add'].call_args.args[0]
        self.assertEqual((added.case_id, added.vulnerability_id, added.note, added.added_by_id), (4, 7, 'edge', 3))

    def test_attach_should_report_invisible_and_existing_assets(self):
        def _get(managed_asset_id, _scope):
            if managed_asset_id == 32:
                raise ObjectNotFoundError()
        self.mocks['get'].side_effect = _get
        self.mocks['find'].side_effect = lambda _room, asset_id, _case, _vuln: object() if asset_id == 31 else None
        out = self._attach({'managed_asset_ids': [30, 31, 32]})
        self.assertEqual(out, {'created': [30], 'existing': [31], 'not_found': [32]})

    def test_attach_should_not_commit_when_nothing_is_new(self):
        self.mocks['find'].return_value = object()
        out = self._attach({'managed_asset_ids': [30]})
        self.assertEqual(out['existing'], [30])
        self.mocks['commit'].assert_not_called()
        self.mocks['track'].assert_not_called()

    def test_attach_should_refuse_a_case_the_caller_cannot_read(self):
        with self.assertRaises(BusinessProcessingError):
            self._attach({'managed_asset_ids': [30], 'case_id': 5})
        self.mocks['add'].assert_not_called()

    def test_attach_should_refuse_a_vulnerability_without_the_permission(self):
        with self.assertRaises(BusinessProcessingError):
            self._attach({'managed_asset_ids': [30], 'vulnerability_id': 7}, can_link=False)

    def test_attach_should_refuse_an_unknown_vulnerability(self):
        self.mocks['vuln'].return_value = None
        with self.assertRaises(BusinessProcessingError):
            self._attach({'managed_asset_ids': [30], 'vulnerability_id': 8})

    def test_attach_should_validate_the_ids(self):
        for body in ({}, {'managed_asset_ids': []}, {'managed_asset_ids': ['30']},
                     {'managed_asset_ids': [True]}, {'managed_asset_ids': list(range(201))}):
            with self.assertRaises(BusinessProcessingError):
                self._attach(body)

    def test_attach_should_cap_the_room(self):
        self.mocks['count'].return_value = biz.WAR_ROOM_MANAGED_ASSETS_MAX_PER_ROOM
        with self.assertRaises(BusinessProcessingError):
            self._attach({'managed_asset_ids': [30]})

    def test_attach_should_report_a_concurrent_attach(self):
        self.mocks['commit'].return_value = False
        with self.assertRaises(BusinessProcessingError):
            self._attach({'managed_asset_ids': [30]})


class TestUpdateDetach(TestCase):

    def setUp(self):
        self.attachment = SimpleNamespace(id=1, managed_asset_id=30, case_id=4, vulnerability_id=None, note=None)
        self.mocks = {}
        patches = {
            'get': patch(f'{_BIZ}.war_room_managed_assets_db_get', return_value=self.attachment),
            'asset': patch(f'{_BIZ}.managed_assets_get'),
            'commit': patch(f'{_BIZ}.war_room_managed_assets_db_commit', return_value=True),
            'delete': patch(f'{_BIZ}.war_room_managed_assets_db_delete'),
            'vuln': patch(f'{_BIZ}.vulnerabilities_db_get', return_value=_vulnerability()),
            'track': patch(f'{_BIZ}.track_activity'),
        }
        for name, patcher in patches.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_update_should_change_only_the_given_links(self):
        biz.war_room_managed_assets_update(9, 1, {'vulnerability_id': 7}, 'scope', [4], True)
        self.assertEqual((self.attachment.case_id, self.attachment.vulnerability_id), (4, 7))
        biz.war_room_managed_assets_update(9, 1, {'case_id': None, 'note': 'x'}, 'scope', [4], True)
        self.assertEqual((self.attachment.case_id, self.attachment.note), (None, 'x'))

    def test_update_should_hide_attachments_of_unreadable_cases(self):
        with self.assertRaises(ObjectNotFoundError):
            biz.war_room_managed_assets_update(9, 1, {'note': 'x'}, 'scope', [5], True)

    def test_update_should_hide_attachments_of_invisible_assets(self):
        self.mocks['asset'].side_effect = ObjectNotFoundError()
        with self.assertRaises(ObjectNotFoundError):
            biz.war_room_managed_assets_detach(9, 1, 'scope', [4])
        self.mocks['delete'].assert_not_called()

    def test_update_should_refuse_the_vulnerability_link_without_the_permission(self):
        with self.assertRaises(BusinessProcessingError):
            biz.war_room_managed_assets_update(9, 1, {'vulnerability_id': None}, 'scope', [4], False)

    def test_update_should_report_a_duplicate(self):
        self.mocks['commit'].return_value = False
        with self.assertRaises(BusinessProcessingError):
            biz.war_room_managed_assets_update(9, 1, {'case_id': None}, 'scope', [4], True)

    def test_detach_should_delete_and_log(self):
        biz.war_room_managed_assets_detach(9, 1, 'scope', [4])
        self.mocks['delete'].assert_called_once_with(self.attachment)
        self.mocks['track'].assert_called_once()

    def test_detach_should_404_for_another_room(self):
        self.mocks['get'].return_value = None
        with self.assertRaises(ObjectNotFoundError):
            biz.war_room_managed_assets_detach(9, 2, 'scope', [4])
