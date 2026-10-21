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

"""Integration tests for `/api/v2/war-rooms/<id>/tracker-steps`,
`/api/v2/war-rooms/<id>/trackers` and `/api/v2/manage/tracker-templates`."""

from unittest import TestCase

from iris import Iris

_WAR_ROOMS_READ_WRITE = 0x8000 | 0x10000


class TestsRestWarRoomTrackers(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        templates = self._subject.get('/api/v2/manage/tracker-templates').json()
        if isinstance(templates, list):
            for template in templates:
                self._subject.delete(f"/api/v2/manage/tracker-templates/{template['id']}")
        self._subject.clear_database()

    def _room(self, name='Trackers Room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

    def _attach_case(self, room_id):
        case_id = self._subject.create_dummy_case()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})
        return case_id

    def _asset(self, case_id, name):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']

    def _ioc(self, case_id, value):
        return self._subject.create(f'/api/v2/cases/{case_id}/iocs',
                                    {'ioc_type_id': 1, 'ioc_tlp_id': 2, 'ioc_value': value}).json()['ioc_id']

    def _step(self, room_id, name, **body):
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/tracker-steps', {'name': name, **body})

    def _tracker(self, room_id, source='manual', **body):
        body.setdefault('name', f'{source} tracker')
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers', {'source': source, **body})

    def _member(self, room_id):
        member = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members', {'user_id': member.get_identifier()})
        return member

    # ------------------------------------------------------------- Steps --

    def test_create_step_should_return_201(self):
        room_id = self._room()
        response = self._step(room_id, 'Isolated', color='red', icon='shield-off')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(('Isolated', 'red', 'shield-off', 0), (body['name'], body['color'], body['icon'],
                                                                body['sort_order']))

    def test_create_step_should_reject_a_duplicated_name(self):
        room_id = self._room()
        self._step(room_id, 'Isolated')
        self.assertEqual(400, self._step(room_id, 'isolated').status_code)

    def test_create_step_should_reject_an_unknown_color(self):
        room_id = self._room()
        self.assertEqual(400, self._step(room_id, 'Isolated', color='#ff0000').status_code)

    def test_reorder_steps_should_return_them_in_order(self):
        room_id = self._room()
        first = self._step(room_id, 'A').json()['id']
        second = self._step(room_id, 'B').json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/tracker-steps/reorder', {'ids': [second]})
        self.assertEqual(200, response.status_code)
        self.assertEqual([second, first], [step['id'] for step in response.json()])

    def test_delete_step_should_prune_it_from_the_trackers(self):
        room_id = self._room()
        step_id = self._step(room_id, 'A').json()['id']
        tracker_id = self._tracker(room_id, step_ids=[step_id]).json()['id']
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/tracker-steps/{step_id}')
        self.assertEqual(204, response.status_code)
        tracker = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}').json()
        self.assertEqual([], tracker['step_ids'])

    # ---------------------------------------------------------- Trackers --

    def test_create_tracker_should_return_201(self):
        room_id = self._room()
        response = self._tracker(room_id, 'assets', columns=[{'label': 'Ticket', 'type': 'url'}],
                                 show_asset_flags=True)
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual('assets', body['source'])
        self.assertEqual('c_ticket', body['columns'][0]['key'])
        self.assertTrue(body['show_asset_flags'])
        self.assertIsNone(body['step_ids'])
        self.assertEqual(0, body['row_count'])

    def test_create_tracker_should_reject_an_unknown_source(self):
        room_id = self._room()
        self.assertEqual(400, self._tracker(room_id, 'vulnerabilities').status_code)

    def test_update_tracker_should_refuse_to_change_the_source(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id).json()['id']
        response = self._subject.patch(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}', {'source': 'assets'})
        self.assertEqual(400, response.status_code)
        response = self._subject.patch(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}', {'name': 'Renamed'})
        self.assertEqual('Renamed', response.json()['name'])

    def test_get_tracker_of_another_room_should_return_404(self):
        room_id = self._room()
        other_id = self._room('Other')
        tracker_id = self._tracker(room_id).json()['id']
        response = self._subject.get(f'/api/v2/war-rooms/{other_id}/trackers/{tracker_id}')
        self.assertEqual(404, response.status_code)

    def test_delete_tracker_should_return_204(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id).json()['id']
        self.assertEqual(204, self._subject.delete(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}').status_code)
        self.assertEqual([], self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers').json())

    # -------------------------------------------------------------- Rows --

    def test_mirror_should_list_the_assets_of_the_attached_cases(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        tracker_id = self._tracker(room_id, 'assets').json()['id']
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows')
        self.assertEqual(200, response.status_code)
        rows = response.json()['data']
        self.assertEqual([f'asset:{asset_id}'], [row['key'] for row in rows])
        self.assertIsNone(rows[0]['row_id'])
        self.assertEqual('dc01', rows[0]['label'])
        trackers = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers').json()
        self.assertEqual(1, trackers[0]['row_count'])

    def test_state_should_store_steps_and_values_of_a_mirrored_item(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker = self._tracker(room_id, 'assets', columns=[{'label': 'Due', 'type': 'date'}]).json()
        key = tracker['columns'][0]['key']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}], 'add_step_ids': [step_id], 'note': 'Done',
            'values': {key: '2026-10-21'},
        })
        self.assertEqual(200, response.status_code)
        row = response.json()['data'][0]
        self.assertIsNotNone(row['row_id'])
        self.assertEqual([step_id], [step['step_id'] for step in row['steps']])
        self.assertEqual({key: '2026-10-21'}, row['values'])
        history = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/history',
                                    {'item_type': 'asset', 'item_id': asset_id}).json()
        self.assertEqual({'step_added', 'note_changed', 'value_changed'}, {event['action'] for event in history})

    def test_state_should_post_a_line_in_the_war_room_stream(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        step_id = self._step(room_id, 'Isolated').json()['id']
        tracker = self._tracker(room_id, 'assets', name='Hosts').json()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}], 'add_step_ids': [step_id]})
        listed = self._subject.get(f'/api/v2/war-rooms/{room_id}/chat').json()
        lines = [(m['body'], m['ref_type'], m['ref_id']) for m in listed if m['kind'] == 'tracker']
        self.assertIn(('Hosts: set Isolated on dc01', 'war_room_tracker', tracker['id']), lines)
        self.assertIn(('Created tracker: Hosts', 'war_room_tracker', tracker['id']), lines)

    def test_state_should_reject_an_item_out_of_the_room(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'elsewhere')
        tracker_id = self._tracker(room_id, 'assets').json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}], 'note': 'x'})
        self.assertEqual(400, response.status_code)

    def test_state_should_reject_an_invalid_value(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'dc01')
        tracker = self._tracker(room_id, 'assets', columns=[{'label': 'Due', 'type': 'date'}]).json()
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker["id"]}/rows/state', {
            'rows': [{'item_type': 'asset', 'item_id': asset_id}],
            'values': {tracker['columns'][0]['key']: 'tomorrow'}})
        self.assertEqual(400, response.status_code)

    def test_iocs_with_the_same_value_should_be_one_row(self):
        room_id = self._room()
        first_case = self._attach_case(room_id)
        second_case = self._attach_case(room_id)
        self._ioc(first_case, 'evil.example')
        self._ioc(second_case, 'evil.example')
        tracker_id = self._tracker(room_id, 'iocs').json()['id']
        rows = self._subject.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows').json()['data']
        self.assertEqual(1, len(rows))
        self.assertEqual(2, len(rows[0]['item']['case_ids']))

    def test_manual_rows_should_be_added_reordered_and_deleted(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id).json()['id']
        url = f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows'
        response = self._subject.create(url, {'rows': [{'label': 'Call the CERT'},
                                                       {'item_type': 'case', 'item_id': case_id}]})
        self.assertEqual(201, response.status_code)
        created = response.json()['data']
        self.assertEqual(2, len(created))
        again = self._subject.create(url, {'rows': [{'item_type': 'case', 'item_id': case_id}]}).json()['data']
        self.assertEqual([], again)
        ids = [row['row_id'] for row in created]
        self.assertEqual(204, self._subject.create(f'{url}/reorder', {'ids': [ids[1]]}).status_code)
        rows = self._subject.get(url).json()['data']
        self.assertEqual([ids[1], ids[0]], [row['row_id'] for row in rows])
        self.assertEqual(204, self._subject.delete(f'{url}/{ids[0]}').status_code)
        self.assertEqual(1, len(self._subject.get(url).json()['data']))

    def test_manual_rows_should_reject_an_item_out_of_the_room(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        tracker_id = self._tracker(room_id).json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'item_type': 'case', 'item_id': case_id}]})
        self.assertEqual(400, response.status_code)

    def test_rows_cannot_be_added_to_a_mirror(self):
        room_id = self._room()
        tracker_id = self._tracker(room_id, 'cases').json()['id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                                        {'rows': [{'label': 'x'}]})
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------ Access --

    def test_a_member_without_case_access_should_see_restricted_rows(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        tracker_id = self._tracker(room_id).json()['id']
        self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows',
                             {'rows': [{'item_type': 'case', 'item_id': case_id}]})
        member = self._member(room_id)
        rows = member.get(f'/api/v2/war-rooms/{room_id}/trackers/{tracker_id}/rows').json()['data']
        self.assertTrue(rows[0]['restricted'])
        self.assertIsNone(rows[0]['item'])

    def test_a_non_member_should_be_denied(self):
        room_id = self._room()
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self.assertEqual(403, user.get(f'/api/v2/war-rooms/{room_id}/trackers').status_code)
        self.assertEqual(403, user.create(f'/api/v2/war-rooms/{room_id}/tracker-steps', {'name': 'x'}).status_code)

    # --------------------------------------------------------- Templates --

    def test_template_should_be_created_applied_and_exported(self):
        response = self._subject.create('/api/v2/manage/tracker-templates', {
            'name': 'Remediation',
            'steps': [{'name': 'Isolated', 'color': 'red'}, {'name': 'Restored'}],
            'trackers': [{'name': 'Assets', 'source': 'assets', 'step_names': ['Isolated']}],
        })
        self.assertEqual(201, response.status_code)
        template_id = response.json()['id']
        room_id = self._room()
        self._step(room_id, 'isolated')
        applied = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/apply-template',
                                       {'template_id': template_id})
        self.assertEqual(200, applied.status_code)
        body = applied.json()
        self.assertEqual(2, len(body['steps']))
        self.assertEqual(1, len(body['trackers']))
        exported = self._subject.get(f'/api/v2/manage/tracker-templates/{template_id}/export').json()
        self.assertEqual('iris-tracker-template', exported['format'])
        self.assertEqual(400, self._subject.create('/api/v2/manage/tracker-templates/import', exported).status_code)
        exported['name'] = 'Remediation copy'
        self.assertEqual(201, self._subject.create('/api/v2/manage/tracker-templates/import', exported).status_code)

    def test_save_as_template_should_return_201(self):
        room_id = self._room()
        step_id = self._step(room_id, 'Isolated').json()['id']
        self._tracker(room_id, 'assets', step_ids=[step_id])
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/trackers/save-as-template',
                                        {'name': 'From room'})
        self.assertEqual(201, response.status_code)
        self.assertEqual(['Isolated'], response.json()['trackers'][0]['step_names'])

    def test_preset_should_create_a_template(self):
        presets = self._subject.get('/api/v2/manage/tracker-templates/presets').json()
        self.assertIn('customer-notification', [preset['key'] for preset in presets])
        response = self._subject.create('/api/v2/manage/tracker-templates/presets/customer-notification', {})
        self.assertEqual(201, response.status_code)
        self.assertEqual(5, len(response.json()['steps']))

    def test_template_changes_should_need_server_administrator(self):
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self.assertEqual(200, user.get('/api/v2/manage/tracker-templates').status_code)
        self.assertEqual(403, user.create('/api/v2/manage/tracker-templates', {'name': 'x'}).status_code)
