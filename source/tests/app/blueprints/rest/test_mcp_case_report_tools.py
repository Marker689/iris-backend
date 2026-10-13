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

"""Unit tests for the MCP tools a case report is built from: the timeline
events, the paged notes, the evidences and the datastore file write. The business layer is
mocked; these tests pin the projection, the paging and the wiring."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp import classification
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.tools import datastore as datastore_tools
from app.blueprints.rest.v2.mcp.tools import events as events_tools
from app.blueprints.rest.v2.mcp.tools import evidences as evidences_tools
from app.blueprints.rest.v2.mcp.tools import notes as notes_tools
from app.business import datastore as datastore_business
from app.models.errors import BusinessProcessingError


_EVENTS = 'app.blueprints.rest.v2.mcp.tools.events'
_NOTES = 'app.blueprints.rest.v2.mcp.tools.notes'
_EVIDENCES = 'app.blueprints.rest.v2.mcp.tools.evidences'
_DATASTORE = 'app.blueprints.rest.v2.mcp.tools.datastore'
_DATASTORE_BUSINESS = 'app.business.datastore'


def _event(event_id, **kwargs):
    event = {'event_id': event_id, 'event_date': f'2026-01-0{event_id}T10:00:00', 'event_tz': '+00:00',
             'event_title': f'event {event_id}', 'category_name': 'Lateral movement', 'event_tags': 'x',
             'event_content': 'short', 'event_in_summary': False, 'event_is_flagged': False,
             'parent_event_id': None,
             'assets': [{'id': 1, 'asset_name': 'WS01', 'asset_type': 'Windows - Computer', 'compromised': True}],
             'iocs': [{'id': 2, 'ioc_value': 'evil.example', 'ioc_type': 'domain'}],
             'event_raw': 'not projected'}
    event.update(kwargs)
    return event


class TestCaseEventsList(TestCase):

    def _run(self, events, **args):
        list_filtered = MagicMock(return_value={'tim': events, 'categories': []})
        with patch(f'{_EVENTS}.events_list_filtered', list_filtered):
            result = events_tools.iris_case_events_list({'case_identifier': 4, **args})
        return result, list_filtered

    def test_reads_every_event_of_the_case(self):
        _, list_filtered = self._run([_event(1)])
        list_filtered.assert_called_once_with(4, {}, page=1, per_page=0)

    def test_rows_are_projected(self):
        result, _ = self._run([_event(1)])
        row = result['data'][0]
        self.assertNotIn('event_raw', row)
        self.assertEqual([{'asset_id': 1, 'asset_name': 'WS01', 'asset_type': 'Windows - Computer',
                           'compromised': True}], row['assets'])
        self.assertEqual([{'ioc_id': 2, 'ioc_value': 'evil.example'}], row['iocs'])

    def test_pages_without_repeating_events(self):
        events = [_event(i) for i in range(1, 6)]
        first, _ = self._run(events, page=1, per_page=2)
        last, _ = self._run(events, page=3, per_page=2)
        self.assertEqual([1, 2], [row['event_id'] for row in first['data']])
        self.assertEqual(2, first['next_page'])
        self.assertEqual(3, first['last_page'])
        self.assertEqual(5, first['total'])
        self.assertEqual([5], [row['event_id'] for row in last['data']])
        self.assertIsNone(last['next_page'])

    def test_in_summary_keeps_the_summary_events(self):
        result, _ = self._run([_event(1), _event(2, event_in_summary=True)], in_summary=True)
        self.assertEqual([2], [row['event_id'] for row in result['data']])
        self.assertEqual(1, result['total'])

    def test_content_is_clipped_unless_the_full_view_is_asked(self):
        events = [_event(1, event_content='a' * 1000)]
        summary, _ = self._run(events)
        full, _ = self._run(events, view='full')
        self.assertLess(len(summary['data'][0]['event_content']), 1000)
        self.assertEqual('a' * 1000, full['data'][0]['event_content'])

    def test_events_are_ordered_by_date_then_id(self):
        events = [_event(3, event_date='2026-01-02T00:00:00'), _event(2, event_date='2026-01-01T00:00:00'),
                  _event(1, event_date='2026-01-02T00:00:00')]
        result, _ = self._run(events)
        self.assertEqual([2, 1, 3], [row['event_id'] for row in result['data']])

    def test_no_event(self):
        result, _ = self._run([])
        self.assertEqual({'total': 0, 'data': [], 'last_page': 1, 'current_page': 1, 'next_page': None}, result)


class TestCaseNotesListPaging(TestCase):

    def _run(self, **args):
        notes = [SimpleNamespace(note_id=i) for i in (3, 1, 2)]
        schema = MagicMock(dump=MagicMock(side_effect=lambda rows, **_kwargs: [{'note_id': n.note_id} for n in rows]))
        with patch(f'{_NOTES}.notes_search', MagicMock(return_value=notes)), \
                patch(f'{_NOTES}.schema_for_view', MagicMock(return_value=schema)):
            return notes_tools.iris_case_notes_list({'case_identifier': 4, **args})

    def test_every_note_without_paging(self):
        self.assertEqual({'notes': [{'note_id': 3}, {'note_id': 1}, {'note_id': 2}]}, self._run())

    def test_pages_by_note_id(self):
        first = self._run(page=1, per_page=2)
        last = self._run(page=2, per_page=2)
        self.assertEqual([1, 2], [row['note_id'] for row in first['notes']])
        self.assertEqual({'total': 3, 'last_page': 2, 'current_page': 1, 'next_page': 2},
                         {k: v for k, v in first.items() if k != 'notes'})
        self.assertEqual([3], [row['note_id'] for row in last['notes']])
        self.assertIsNone(last['next_page'])


class TestCaseNotesGetWindow(TestCase):

    def _run(self, content, **args):
        note = SimpleNamespace(note_id=7, note_case_id=4, note_title='Investigation', directory_id=2)
        schema = MagicMock(dump=MagicMock(return_value={'note_id': 7, 'note_content': 'stale'}))
        with patch(f'{_NOTES}.notes_get', MagicMock(return_value=note)), \
                patch(f'{_NOTES}._note_schema', schema), \
                patch(f'{_NOTES}.collab_current_markdown', MagicMock(return_value=content)):
            return notes_tools.iris_case_notes_get({'case_identifier': 4, 'note_identifier': 7, **args})

    def test_whole_note_without_offset(self):
        self.assertEqual({'note_id': 7, 'note_content': 'live'}, self._run('live'))

    def test_window_in_chunks_under_the_string_clip(self):
        content = ''.join(str(i % 10) for i in range(70000))
        first = self._run(content, content_offset=0)
        self.assertEqual(70000, first['content_length'])
        self.assertEqual(30000, first['content_next_offset'])
        self.assertEqual(10, len(first['note_content_chunks']))
        self.assertTrue(all(len(chunk) <= 3000 for chunk in first['note_content_chunks']))
        self.assertNotIn('note_content', first)
        last = self._run(content, content_offset=60000)
        self.assertIsNone(last['content_next_offset'])
        self.assertEqual(content[60000:], ''.join(last['note_content_chunks']))

    def test_window_passes_the_result_budget(self):
        from app.blueprints.rest.v2.mcp.result_budget import apply_result_budget
        window = self._run('é' * 30000 + '\n' * 10, content_offset=0)
        result, report = apply_result_budget(window)
        received = ''.join(result['note_content_chunks'])
        # Escaped rows dropped off the end, never clipped: a resumable prefix
        self.assertIsNotNone(report)
        self.assertTrue(('é' * 30000).startswith(received))
        self.assertGreater(len(received), 0)

    def test_offset_past_the_end(self):
        result = self._run('short', content_offset=99)
        self.assertEqual([], result['note_content_chunks'])
        self.assertIsNone(result['content_next_offset'])


class TestCaseEvidencesList(TestCase):

    def _run(self, **args):
        evidences_filter = MagicMock(return_value=SimpleNamespace(items=[], total=0, pages=1, page=1,
                                                                  next_num=None, has_next=False))
        with patch(f'{_EVIDENCES}.evidences_filter', evidences_filter):
            evidences_tools.iris_case_evidences_list({'case_identifier': 4, **args})
        return evidences_filter

    def test_orders_by_date_added_by_default(self):
        evidences_filter = self._run()
        case_identifier, pagination = evidences_filter.call_args.args
        self.assertEqual(4, case_identifier)
        self.assertEqual('date_added', pagination.get_order_by())

    def test_keeps_the_requested_order(self):
        _, pagination = self._run(order_by='filename').call_args.args
        self.assertEqual('filename', pagination.get_order_by())

    def test_business_error_is_an_invalid_parameter(self):
        with patch(f'{_EVIDENCES}.evidences_filter', MagicMock(side_effect=BusinessProcessingError('bad'))):
            with self.assertRaises(MCPError):
                evidences_tools.iris_case_evidences_list({'case_identifier': 4})


class TestCaseDatastoreFilesCreate(TestCase):

    def _run(self, args, demo=False):
        dsf = SimpleNamespace(file_id=12, file_original_name='report.md')
        create = MagicMock(return_value=dsf)
        with patch(f'{_DATASTORE}.iris_current_user', SimpleNamespace(id=5)), \
                patch(f'{_DATASTORE}.datastore_create_file', create), \
                patch(f'{_DATASTORE}.is_demo_mode_enabled', MagicMock(return_value=demo)), \
                patch(f'{_DATASTORE}.track_activity', MagicMock()), \
                patch(f'{_DATASTORE}._file_schema', MagicMock(dump=MagicMock(return_value={'file_id': 12}))):
            result = datastore_tools.iris_case_datastore_files_create({'case_identifier': 4, **args})
        return result, create

    def test_writes_the_content_as_utf8(self):
        result, create = self._run({'filename': 'report.md', 'content': '# Rapport é', 'folder': 'Reports',
                                    'description': 'Case report', 'tags': 'report'})
        create.assert_called_once_with(4, 5, 'report.md', '# Rapport é'.encode('utf-8'),
                                       description='Case report', tags='report', folder_name='Reports')
        self.assertEqual('/api/v2/cases/4/datastore/files/12', result['file_url'])

    def test_without_folder_the_file_goes_to_the_root(self):
        _, create = self._run({'filename': 'report.md', 'content': 'x'})
        self.assertIsNone(create.call_args.kwargs['folder_name'])

    def test_demo_mode_caps_the_size(self):
        with patch(f'{_DATASTORE}.DEMO_MODE_UPLOAD_MAX_BYTES', 3):
            with self.assertRaises(MCPError):
                self._run({'filename': 'report.md', 'content': 'four'}, demo=True)

    def test_business_error_is_an_invalid_parameter(self):
        with patch(f'{_DATASTORE}.iris_current_user', SimpleNamespace(id=5)), \
                patch(f'{_DATASTORE}.is_demo_mode_enabled', MagicMock(return_value=False)), \
                patch(f'{_DATASTORE}.datastore_create_file', MagicMock(side_effect=BusinessProcessingError('bad'))):
            with self.assertRaises(MCPError):
                datastore_tools.iris_case_datastore_files_create({'case_identifier': 4, 'filename': '/',
                                                                 'content': 'x'})

    def test_classification(self):
        self.assertIn('iris_case_datastore_files_create', classification.WRITE_TOOLS)
        self.assertIn('iris_case_events_list', classification.READ_ONLY_TOOLS)
        self.assertIn('iris_case_evidences_list', classification.READ_ONLY_TOOLS)


class TestDatastoreCreateFile(TestCase):

    def _run(self, filename='report.md', content=b'# Report', folder_name=None):
        store = MagicMock(side_effect=lambda dsf, *_args: dsf)
        folder = MagicMock(return_value=SimpleNamespace(path_id=30))
        root = MagicMock(return_value=SimpleNamespace(path_id=1))
        hook = MagicMock()
        with patch(f'{_DATASTORE_BUSINESS}.datastore_store_new_file', store), \
                patch(f'{_DATASTORE_BUSINESS}.datastore_get_or_create_folder', folder), \
                patch(f'{_DATASTORE_BUSINESS}.datastore_get_root', root), \
                patch(f'{_DATASTORE_BUSINESS}.add_obj_history_entry', MagicMock()), \
                patch(f'{_DATASTORE_BUSINESS}.call_modules_hook', hook):
            dsf = datastore_business.datastore_create_file(4, 5, filename, content, folder_name=folder_name)
        return dsf, store, folder, hook

    def test_stores_the_file_in_the_root_and_fires_the_upload_hook(self):
        dsf, store, folder, hook = self._run()
        self.assertEqual(1, dsf.file_parent_id)
        self.assertEqual(5, dsf.added_by_user_id)
        self.assertEqual(b'# Report', store.call_args.args[1])
        folder.assert_not_called()
        hook.assert_called_once_with('on_postload_datastore_file_create', data=dsf, caseid=4)

    def test_stores_the_file_in_the_folder(self):
        dsf, _, folder, _ = self._run(folder_name='Reports')
        folder.assert_called_once_with('Reports', 4)
        self.assertEqual(30, dsf.file_parent_id)

    def test_path_separators_are_neutralised(self):
        dsf, _, folder, _ = self._run(filename='../../etc/passwd', folder_name='a/b')
        self.assertEqual('.._.._etc_passwd', dsf.file_original_name)
        folder.assert_called_once_with('a_b', 4)

    def test_rejects_an_empty_name(self):
        for filename in ('', '  ', '..', '/'):
            with self.subTest(filename=filename), self.assertRaises(BusinessProcessingError):
                self._run(filename=filename)

    def test_rejects_a_file_too_large(self):
        with patch(f'{_DATASTORE_BUSINESS}.DATASTORE_CREATE_FILE_MAX_BYTES', 3):
            with self.assertRaises(BusinessProcessingError):
                self._run(content=b'four')

    def test_storage_failure_is_a_business_error(self):
        with patch(f'{_DATASTORE_BUSINESS}.datastore_store_new_file', MagicMock(side_effect=OSError('disk full'))), \
                patch(f'{_DATASTORE_BUSINESS}.datastore_get_root', MagicMock(return_value=SimpleNamespace(path_id=1))), \
                patch(f'{_DATASTORE_BUSINESS}.add_obj_history_entry', MagicMock()), \
                patch(f'{_DATASTORE_BUSINESS}.call_modules_hook', MagicMock()) as hook:
            with self.assertRaises(BusinessProcessingError):
                datastore_business.datastore_create_file(4, 5, 'report.md', b'x')
        hook.assert_not_called()
