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

"""The workflow library: shipped entries listed with the published ones,
publishing, updating and removing an entry. DB helpers are patched."""

import uuid
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.ai_workflow_library import ai_workflow_library_delete
from app.business.ai_workflow_library import ai_workflow_library_list
from app.business.ai_workflow_library import ai_workflow_library_publish
from app.business.ai_workflow_library import ai_workflow_library_update
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_import
from app.iris_engine.ai_workflows.library import ai_workflows_library_entries
from app.models.ai_workflows import AiWorkflowLibraryEntry
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from tests.app.business.test_ai_workflows import _ADMIN
from tests.app.business.test_ai_workflows import _OTHER
from tests.app.business.test_ai_workflows import _OWNER
from tests.app.business.test_ai_workflows import _body
from tests.app.business.test_ai_workflows import _stored_workflow
from tests.app.business.test_ai_workflows_portable import _NOW
from tests.app.business.test_ai_workflows_portable import _PortableTestCase
from tests.app.business.test_ai_workflows_portable import _TOKEN
from tests.app.business.test_ai_workflows_portable import _graph

_LIBRARY = 'app.business.ai_workflow_library'


def _document(**overrides):
    return {'format': 'iris-ai-workflow', 'format_version': 1, 'category': 'Triage',
            'workflow': _body(**overrides)}


class TestsAiWorkflowLibrary(_PortableTestCase):

    def setUp(self):
        super().setUp()
        self.entries = {}
        self.deleted = []
        self.track = MagicMock()

        def _add(entry):
            self.added.append(entry)
            if isinstance(entry, AiWorkflowLibraryEntry) and entry.id is None:
                entry.id = 40 + len(self.entries)
                entry.uuid = uuid.uuid4()
                self.entries[entry.id] = entry

        for target, replacement in (
                (f'{_LIBRARY}.ai_workflows_library_db_get', lambda i: self.entries.get(i)),
                (f'{_LIBRARY}.ai_workflows_library_db_list', lambda: list(self.entries.values())),
                (f'{_LIBRARY}.ai_workflows_db_add', _add),
                (f'{_LIBRARY}.ai_workflows_db_commit', lambda: None),
                (f'{_LIBRARY}.ai_workflows_db_delete', self.deleted.append),
                (f'{_LIBRARY}.ai_workflows_db_user_summary',
                 lambda ids: {i: {'id': i, 'name': f'user {i}'} for i in ids}),
                (f'{_LIBRARY}.ai_workflows_db_utcnow', lambda: _NOW),
                (f'{_LIBRARY}.track_activity', self.track)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _publish(self, body=None, user_id=_OWNER, is_admin=False):
        return ai_workflow_library_publish(body or {'document': _document()}, user_id, is_admin)

    def _invalid(self, operation):
        with self.assertRaises(BusinessProcessingError) as context:
            operation()
        return [e['field'] for e in context.exception.get_data()['errors']]

    def test_publish_from_a_workflow_should_store_its_export(self):
        self.workflows[3] = _stored_workflow(graph=_graph(), customer_scope=[2])
        entry = self._publish({'workflow_id': 3, 'category': 'Enrichment', 'description': ' Looks things up '})
        self.assertEqual(('user-40', 40, 'user', 'Stored', 'Looks things up', 'Enrichment', _OWNER, True),
                         (entry['id'], entry['entry_id'], entry['source'], entry['name'], entry['description'],
                          entry['category'], entry['owner_id'], entry['can_edit']))
        stored = self.entries[40].document
        self.assertEqual(('iris-ai-workflow', 'Enrichment', 'Stored'),
                         (stored['format'], stored['category'], stored['workflow']['name']))
        self.assertNotIn('customer_scope', stored['workflow'])
        self.assertNotIn('owner_id', stored['workflow'])
        self.assertEqual({'id': _OWNER, 'name': f'user {_OWNER}'}, entry['owner'])
        self.track.assert_called_once()

    def test_publish_should_never_store_a_literal_credential(self):
        self._publish({'document': _document(graph=_graph())})
        self.assertNotIn(_TOKEN, str(self.entries[40].document))
        self.assertEqual(['CALL_AUTH_SECRET', 'VT'], self.entries[40].document['requirements']['keystore'])

    def test_publish_of_a_workflow_of_someone_else_should_be_not_found(self):
        self.workflows[3] = _stored_workflow()
        with self.assertRaises(ObjectNotFoundError):
            self._publish({'workflow_id': 3}, user_id=_OTHER)
        self.assertEqual({}, self.entries)

    def test_publish_should_refuse_what_is_not_a_valid_workflow(self):
        self.assertEqual(['document'], self._invalid(lambda: self._publish({'name': 'X'})))
        self.assertEqual(['workflow_id'], self._invalid(lambda: self._publish({'workflow_id': '3'})))
        self.assertEqual(['document'], self._invalid(lambda: self._publish({'document': {
            'format': 'iris-ai-workflow-block', 'format_version': 1, 'block': {}}})))
        self.assertEqual(['name'], self._invalid(lambda: self._publish({'document': _document(), 'name': ' '})))
        self.assertEqual(['category'], self._invalid(lambda: self._publish({'document': _document(),
                                                                            'category': 'x' * 65})))
        self.assertEqual(['trigger_type'], self._invalid(lambda: self._publish({'document': _document(
            trigger_type='daily')})))
        self.graph_errors = [{'node_id': 'a', 'field': None, 'message': 'orphan'}]
        self.assertEqual([None], self._invalid(lambda: self._publish()))
        self.assertEqual({}, self.entries)

    def test_list_should_merge_the_published_entries_into_the_shipped_ones(self):
        self._publish({'document': _document(), 'name': 'AAA first'})
        entries = ai_workflow_library_list(_OTHER, False)
        shipped = ai_workflows_library_entries()
        self.assertEqual(len(shipped) + 1, len(entries))
        keys = [(e['category'].lower(), e['name'].lower()) for e in entries]
        self.assertEqual(sorted(keys), keys)
        [mine] = [e for e in entries if e['source'] == 'user']
        self.assertEqual(('user-40', 'AAA first', 'Triage', False), (mine['id'], mine['name'], mine['category'],
                                                                       mine['can_edit']))
        self.assertEqual({'shipped'}, {e['source'] for e in entries if e['id'] != 'user-40'})
        self.assertFalse(any(e['can_edit'] for e in entries if e['source'] == 'shipped'))
        self.assertTrue([e for e in ai_workflow_library_list(_ADMIN, True) if e['id'] == 'user-40'][0]['can_edit'])

    def test_list_should_carry_what_the_caller_lacks(self):
        calls = []

        def _warnings(user_id, nodes, allowlist):
            calls.append((user_id, len(nodes or []), allowlist))
            return [{'field': 'keystore', 'message': 'missing'}] if any(
                n['type'] == 'http_request' for n in nodes or []) else []

        self._publish({'document': _document(graph=_graph())})
        with patch(f'{_LIBRARY}.ai_workflows_import_warnings', _warnings):
            entries = {e['id']: e for e in ai_workflow_library_list(5, False)}
        self.assertEqual([{'field': 'keystore', 'message': 'missing'}], entries['ioc_estate_hunt']['warnings'])
        self.assertEqual([{'field': 'keystore', 'message': 'missing'}], entries['user-40']['warnings'])
        self.assertEqual([], entries['case_kickoff']['warnings'])
        self.assertEqual({5}, {c[0] for c in calls})
        self.assertEqual(len(entries), len(calls))

    def test_published_entry_should_import(self):
        self._publish()
        [entry] = [e for e in ai_workflow_library_list(_OTHER, False) if e['source'] == 'user']
        result = ai_workflows_import(entry['document'], _OTHER, False)
        self.assertEqual(('Triage', _OTHER, False), (result['workflow']['name'], result['workflow']['owner_id'],
                                                     result['workflow']['is_active']))

    def test_owner_should_update_the_entry(self):
        self._publish()
        entry = ai_workflow_library_update(40, {'name': 'Renamed', 'category': 'Hunting'}, _OWNER, False)
        self.assertEqual(('Renamed', 'Hunting'), (entry['name'], entry['category']))
        stored = self.entries[40]
        self.assertEqual(('Renamed', 'Hunting', 'Renamed', 'Hunting'),
                         (stored.name, stored.category, stored.document['workflow']['name'],
                          stored.document['category']))

    def test_update_should_take_a_new_document(self):
        self._publish()
        document = _document(name='Edited', description='Better')
        document['category'] = 'Operations'
        entry = ai_workflow_library_update(40, {'document': document}, _OWNER, False)
        self.assertEqual(('Edited', 'Better', 'Operations'), (entry['name'], entry['description'],
                                                              entry['category']))
        self.assertEqual(['document'], self._invalid(lambda: ai_workflow_library_update(40, {'document': 'x'},
                                                                                        _OWNER, False)))
        self.assertEqual('Edited', self.entries[40].name)

    def test_only_the_publisher_or_an_administrator_should_change_an_entry(self):
        self._publish()
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflow_library_update(40, {'name': 'Mine'}, _OTHER, False)
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflow_library_delete(40, _OTHER, False)
        self.assertEqual('Admin', ai_workflow_library_update(40, {'name': 'Admin'}, _ADMIN, True)['name'])
        ai_workflow_library_delete(40, _ADMIN, True)
        self.assertEqual([self.entries[40]], self.deleted)

    def test_entry_of_a_deleted_user_should_be_left_to_administrators(self):
        self._publish()
        self.entries[40].owner_id = None
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflow_library_delete(40, _OWNER, False)
        [entry] = [e for e in ai_workflow_library_list(_ADMIN, True) if e['source'] == 'user']
        self.assertEqual((None, None, True), (entry['owner_id'], entry['owner'], entry['can_edit']))

    def test_unknown_entry_should_be_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflow_library_update(99, {'name': 'X'}, _ADMIN, True)
        with self.assertRaises(ObjectNotFoundError):
            ai_workflow_library_delete(99, _ADMIN, True)
