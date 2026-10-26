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

"""Tracker templates: global bundles of steps and tracker definitions.

Applying one to a war room merges its steps into the room's by name
(case-insensitively, existing steps are kept as they are) and creates
its trackers, in one transaction. Steps are referenced by name inside a
template (`step_names`, null = every step of the room).

A template tracker can come with board widgets (`board_widgets`, the
tracker widgets of `war_room_board_widgets` without their `tracker_id`
and with step names): applying the template adds them to the room's
board, saving a room as a template takes the widgets of its board that
show the saved trackers.

Templates are shared with the case trackers: a template tracker's source
is a war-room or a case source. Applying a template to a war room or a
case creates the trackers whose source it supports and skips the others.

Built-in presets are not seeded: an administrator creates a template
from one through `tracker_templates_create_from_preset`.
"""

import datetime

from app.business.case_trackers import CASE_TRACKERS_MAX_STEPS
from app.business.case_trackers import CASE_TRACKERS_MAX_TRACKERS
from app.business.case_trackers import case_trackers_build_tracker
from app.business.war_room_board_widgets import BREAKDOWN_COLUMN_TYPES
from app.business.war_room_board_widgets import COLUMNS
from app.business.war_room_board_widgets import PROGRESS_DISPLAYS
from app.business.war_room_board_widgets import TRACKER_WIDGET_TYPES
from app.business.war_room_board_widgets import war_room_board_widgets_append
from app.business.war_room_board_widgets import war_room_board_widgets_new_id
from app.business.war_room_board_widgets import war_room_board_widgets_stored
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_validate
from app.business.war_room_trackers import WAR_ROOM_TRACKERS_MAX_STEPS
from app.business.war_room_trackers import WAR_ROOM_TRACKERS_MAX_TRACKERS
from app.business.war_room_trackers import war_room_trackers_build_tracker
from app.business.war_room_trackers import war_room_trackers_validate_color
from app.business.war_room_trackers import war_room_trackers_validate_columns
from app.business.war_room_trackers import war_room_trackers_validate_icon
from app.datamgmt.case.case_trackers_db import case_trackers_db_steps
from app.datamgmt.case.case_trackers_db import case_trackers_db_trackers
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_add
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_commit
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_delete
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_rollback
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_steps
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_template_find_by_name
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_template_get
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_templates
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_trackers
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_trackers_by_ids
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_user_names
from app.iris_engine.utils.tracker import track_activity
from app.models.case_trackers import CASE_TRACKER_SOURCES
from app.models.case_trackers import CaseTrackerStep
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WAR_ROOM_TRACKER_SOURCES
from app.models.war_rooms import TrackerTemplate
from app.models.war_rooms import WarRoomTrackerStep


TRACKER_TEMPLATE_FORMAT = 'iris-tracker-template'
TRACKER_TEMPLATE_FORMAT_VERSION = 1

_NAME_MAX = 128
_STEP_NAME_MAX = 64
_DESCRIPTION_MAX = 4000
_MAX_TEMPLATE_TRACKERS = 20
_MAX_TRACKER_WIDGETS = 10
_WIDGET_TITLE_MAX = 128
_NOTIFIED = ['Email sent', 'Acknowledged', 'Action required', 'Closed']

_PRESETS = {
    'customer-notification': {
        'name': 'Customer notification',
        'description': 'Track the notification of every customer of the war room.',
        'steps': [
            {'name': 'Not contacted', 'color': 'slate', 'icon': 'circle-dashed'},
            {'name': 'Email sent', 'color': 'sky', 'icon': 'mail'},
            {'name': 'Acknowledged', 'color': 'green', 'icon': 'check'},
            {'name': 'Action required', 'color': 'orange', 'icon': 'alert-triangle'},
            {'name': 'Closed', 'color': 'gray', 'icon': 'circle-check'},
        ],
        'trackers': [
            {'name': 'Customer notification', 'icon': 'mail', 'source': 'customers', 'show_asset_flags': False,
             'step_names': ['Not contacted', 'Email sent', 'Acknowledged', 'Action required', 'Closed'],
             'columns': [{'label': 'Contact', 'type': 'text'}, {'label': 'Notified on', 'type': 'date'}],
             'board_widgets': [
                 {'type': 'tracker_count', 'title': 'Contacted',
                  'config': {'step_names': _NOTIFIED, 'of_total': True}},
                 {'type': 'tracker_count', 'title': 'Acknowledged',
                  'config': {'step_names': ['Acknowledged'], 'of_total': True}},
                 {'type': 'tracker_count', 'title': 'Action required',
                  'config': {'step_names': ['Action required'], 'of_total': False}},
                 {'type': 'tracker_progress', 'title': 'Customer notification', 'column': 'main',
                  'config': {'step_names': None, 'display': 'bars'}},
                 {'type': 'tracker_list', 'title': 'Customers with an action required', 'column': 'side',
                  'config': {'step_names': ['Action required']}},
             ]},
        ],
    },
    'asset-remediation': {
        'name': 'Asset remediation',
        'description': 'Track the remediation of every asset of the war room.',
        'steps': [
            {'name': 'To triage', 'color': 'slate', 'icon': 'circle-dashed'},
            {'name': 'Isolated', 'color': 'red', 'icon': 'shield-off'},
            {'name': 'Collected', 'color': 'amber', 'icon': 'hard-drive-download'},
            {'name': 'Reimaged', 'color': 'blue', 'icon': 'refresh-cw'},
            {'name': 'Restored', 'color': 'green', 'icon': 'circle-check'},
        ],
        'trackers': [
            {'name': 'Asset remediation', 'icon': 'wrench', 'source': 'assets', 'show_asset_flags': True,
             'step_names': ['To triage', 'Isolated', 'Collected', 'Reimaged', 'Restored'],
             'columns': [{'label': 'Owner', 'type': 'user'}, {'label': 'Ticket', 'type': 'url'}]},
        ],
    },
}

TRACKER_TEMPLATE_PRESETS = tuple(_PRESETS)

_SOURCES = tuple(dict.fromkeys((*WAR_ROOM_TRACKER_SOURCES, *CASE_TRACKER_SOURCES)))


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _isoformat(value):
    return value.isoformat() if value is not None else None


def _text(value, field, max_len, required=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise BusinessProcessingError(f'{field} is required')
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be a string')
    value = value.strip()
    if len(value) > max_len:
        raise BusinessProcessingError(f'{field} must be at most {max_len} characters')
    return value


def _validate_source(value):
    """A war-room or a case tracker source."""
    if not isinstance(value, str) or value not in _SOURCES:
        raise BusinessProcessingError(f'source must be one of: {", ".join(_SOURCES)}')
    return value


def _validate_steps(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise BusinessProcessingError('steps must be a list')
    if len(value) > WAR_ROOM_TRACKERS_MAX_STEPS:
        raise BusinessProcessingError(f'A template has at most {WAR_ROOM_TRACKERS_MAX_STEPS} steps')
    out = []
    seen = set()
    for step in value:
        if not isinstance(step, dict):
            raise BusinessProcessingError('Each step must be an object')
        name = _text(step.get('name'), 'Step name', _STEP_NAME_MAX, required=True)
        if name.lower() in seen:
            raise BusinessProcessingError(f'Duplicated step "{name}"')
        seen.add(name.lower())
        color = step.get('color')
        out.append({
            'name': name,
            'color': war_room_trackers_validate_color(color) if color is not None else 'slate',
            'icon': war_room_trackers_validate_icon(step.get('icon')),
            'description': _text(step.get('description'), 'description', _DESCRIPTION_MAX),
        })
    return out


def _validate_trackers(value, step_names):
    if value is None:
        return []
    if not isinstance(value, list):
        raise BusinessProcessingError('trackers must be a list')
    if len(value) > _MAX_TEMPLATE_TRACKERS:
        raise BusinessProcessingError(f'A template has at most {_MAX_TEMPLATE_TRACKERS} trackers')
    known = {name.lower(): name for name in step_names}
    out = []
    for tracker in value:
        if not isinstance(tracker, dict):
            raise BusinessProcessingError('Each tracker must be an object')
        names = tracker.get('step_names')
        if names is not None:
            names = _resolve_step_names(names, known, 'step_names')
        show_asset_flags = tracker.get('show_asset_flags', False)
        if not isinstance(show_asset_flags, bool):
            raise BusinessProcessingError('show_asset_flags must be a boolean')
        columns = war_room_trackers_validate_columns(tracker.get('columns'))
        out.append({
            'name': _text(tracker.get('name'), 'Tracker name', _NAME_MAX, required=True),
            'icon': war_room_trackers_validate_icon(tracker.get('icon')),
            'description': _text(tracker.get('description'), 'description', _DESCRIPTION_MAX),
            'source': _validate_source(tracker.get('source')),
            'step_names': names,
            'columns': columns,
            'show_asset_flags': show_asset_flags,
            'timeline_sync': war_room_tracker_timelines_validate(
                _timeline_sync_by_label(tracker.get('timeline_sync'), columns), columns),
            'board_widgets': _validate_board_widgets(tracker.get('board_widgets'), known, columns),
        })
    return out


def _resolve_step_names(names, known, field):
    """Step names matched case-insensitively against the template steps,
    de-duplicated, order kept."""
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise BusinessProcessingError(f'{field} must be a list of step names')
    resolved = []
    for name in names:
        match = known.get(name.strip().lower())
        if match is None:
            raise BusinessProcessingError(f'Unknown step "{name}" in tracker {field}')
        if match not in resolved:
            resolved.append(match)
    return resolved


def _validate_widget_filter(value, columns, label):
    if value is None:
        return None
    keys = {column['key'] for column in columns}
    if not isinstance(value, dict) or value.get('column_key') not in keys:
        raise BusinessProcessingError(f'{label}: the filter column is not a column of the tracker')
    values = value.get('values')
    if not isinstance(values, list) or not values:
        raise BusinessProcessingError(f'{label}: filter values must be a non-empty list')
    return {'column_key': value['column_key'], 'values': values}


def _validate_board_widget(value, known, columns, label):
    """A tracker widget of a template: steps by name, no tracker id. The
    rest is checked again against the room when the template is applied."""
    if not isinstance(value, dict) or value.get('type') not in TRACKER_WIDGET_TYPES:
        raise BusinessProcessingError(f'{label}: type must be one of: {", ".join(TRACKER_WIDGET_TYPES)}')
    widget_type = value['type']
    config = value.get('config') or {}
    if not isinstance(config, dict):
        raise BusinessProcessingError(f'{label}: config must be an object')
    column = value.get('column')
    if column is not None and column not in COLUMNS:
        raise BusinessProcessingError(f'{label}: column must be one of: {", ".join(COLUMNS)}')
    step_names = config.get('step_names')
    if step_names is not None or widget_type in ('tracker_count', 'tracker_list'):
        step_names = _resolve_step_names(step_names or [], known, f'{label} step_names')
    out = {'step_names': step_names}
    if widget_type == 'tracker_progress':
        display = config.get('display') or 'bars'
        if display not in PROGRESS_DISPLAYS:
            raise BusinessProcessingError(f'{label}: display must be one of: {", ".join(PROGRESS_DISPLAYS)}')
        out['display'] = display
    if widget_type == 'tracker_count':
        of_total = config.get('of_total', True)
        if not isinstance(of_total, bool):
            raise BusinessProcessingError(f'{label}: of_total must be a boolean')
        out['of_total'] = of_total
    if widget_type == 'tracker_breakdown':
        key = config.get('column_key')
        if not any(c['key'] == key and c['type'] in BREAKDOWN_COLUMN_TYPES for c in columns):
            raise BusinessProcessingError(f'{label}: column_key must be a select, multiselect or checkbox column '
                                          f'of the tracker')
        out['column_key'] = key
    else:
        out['filter'] = _validate_widget_filter(config.get('filter'), columns, label)
    return {
        'type': widget_type,
        'title': _text(value.get('title'), f'{label} title', _WIDGET_TITLE_MAX),
        'column': column,
        'config': out,
    }


def _validate_board_widgets(value, known, columns):
    """The board widgets a template tracker comes with."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BusinessProcessingError('board_widgets must be a list')
    if len(value) > _MAX_TRACKER_WIDGETS:
        raise BusinessProcessingError(f'A template tracker has at most {_MAX_TRACKER_WIDGETS} board widgets')
    return [_validate_board_widget(widget, known, columns, f'Board widget #{index + 1}')
            for index, widget in enumerate(value)]


def _room_widget(widget, tracker_id, by_name):
    """A template tracker widget turned into a widget of the room's board."""
    config = {key: value for key, value in widget['config'].items() if key != 'step_names'}
    names = widget['config']['step_names']
    config['step_ids'] = [by_name[name.lower()].id for name in names] if names is not None else None
    config['tracker_id'] = tracker_id
    return {'id': war_room_board_widgets_new_id(), 'type': widget['type'], 'title': widget['title'],
            'column': widget['column'], 'config': config}


def _template_widget(widget, step_names):
    """A widget of the room's board turned into a template tracker widget
    (the steps by name)."""
    config = {key: value for key, value in (widget.get('config') or {}).items()
              if key not in ('tracker_id', 'step_ids')}
    step_ids = (widget.get('config') or {}).get('step_ids')
    config['step_names'] = [step_names[step_id] for step_id in step_ids if step_id in step_names] \
        if step_ids is not None else None
    return {'type': widget['type'], 'title': widget.get('title'), 'column': widget.get('column'), 'config': config}


def _timeline_sync_by_label(value, columns):
    """A template is written by hand: its timeline date may name a column
    by label instead of key."""
    if not isinstance(value, dict) or not isinstance(value.get('date'), str):
        return value
    keys = {column['key'] for column in columns}
    if value['date'] in keys:
        return value
    by_label = {column['label'].lower(): column['key'] for column in columns}
    key = by_label.get(value['date'].strip().lower())
    return {**value, 'date': key} if key is not None else value


def tracker_templates_validate_content(data):
    """`(steps, trackers)` of a template payload, normalised."""
    steps = _validate_steps(data.get('steps'))
    trackers = _validate_trackers(data.get('trackers'), [step['name'] for step in steps])
    return steps, trackers


def _validate_name(name, exclude_id=None):
    name = _text(name, 'Template name', _NAME_MAX, required=True)
    if war_room_trackers_db_template_find_by_name(name, exclude_id=exclude_id) is not None:
        raise BusinessProcessingError(f'A template named "{name}" already exists')
    return name


def tracker_templates_serialize(template, names=None):
    if names is None:
        names = war_room_trackers_db_user_names([template.created_by_id])
    return {
        'id': template.id,
        'name': template.name,
        'description': template.description,
        'steps': template.steps or [],
        'trackers': template.trackers or [],
        'created_by': {'id': template.created_by_id, 'name': names.get(template.created_by_id)}
        if template.created_by_id is not None else None,
        'created_at': _isoformat(template.created_at),
        'updated_at': _isoformat(template.updated_at),
    }


def tracker_templates_list():
    templates = war_room_trackers_db_templates()
    names = war_room_trackers_db_user_names([template.created_by_id for template in templates])
    return [tracker_templates_serialize(template, names) for template in templates]


def _get(template_id):
    template = war_room_trackers_db_template_get(template_id)
    if template is None:
        raise ObjectNotFoundError()
    return template


def tracker_templates_get(template_id):
    return tracker_templates_serialize(_get(template_id))


def _save_new(name, description, steps, trackers, user_id):
    now = _utcnow()
    template = war_room_trackers_db_add(TrackerTemplate(
        name=name, description=description, steps=steps, trackers=trackers, created_by_id=user_id,
        created_at=now, updated_at=now,
    ))
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError(f'A template named "{name}" already exists')
    track_activity(f'created tracker template "{name}"', ctx_less=True)
    return template


def tracker_templates_create(data, user_id):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    name = _validate_name(data.get('name'))
    description = _text(data.get('description'), 'description', _DESCRIPTION_MAX)
    steps, trackers = tracker_templates_validate_content(data)
    return tracker_templates_serialize(_save_new(name, description, steps, trackers, user_id))


def tracker_templates_update(template_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    template = _get(template_id)
    if 'name' in data:
        template.name = _validate_name(data['name'], exclude_id=template.id)
    if 'description' in data:
        template.description = _text(data['description'], 'description', _DESCRIPTION_MAX)
    if 'steps' in data or 'trackers' in data:
        steps, trackers = tracker_templates_validate_content({
            'steps': data['steps'] if 'steps' in data else template.steps,
            'trackers': data['trackers'] if 'trackers' in data else template.trackers,
        })
        template.steps = steps
        template.trackers = trackers
    template.updated_at = _utcnow()
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('A template with this name already exists')
    return tracker_templates_serialize(template)


def tracker_templates_delete(template_id):
    template = _get(template_id)
    name = template.name
    war_room_trackers_db_delete(template)
    war_room_trackers_db_commit()
    track_activity(f'deleted tracker template "{name}"', ctx_less=True)


def tracker_templates_export(template_id):
    template = _get(template_id)
    return {
        'format': TRACKER_TEMPLATE_FORMAT,
        'version': TRACKER_TEMPLATE_FORMAT_VERSION,
        'name': template.name,
        'description': template.description,
        'steps': template.steps or [],
        'trackers': template.trackers or [],
    }


def tracker_templates_import(data, user_id):
    """Create a template from an export (`format` / `version` are
    checked when present, other extra keys are ignored)."""
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    if data.get('format') is not None and data['format'] != TRACKER_TEMPLATE_FORMAT:
        raise BusinessProcessingError(f'Not a tracker template (format must be {TRACKER_TEMPLATE_FORMAT})')
    if data.get('version') is not None and data['version'] != TRACKER_TEMPLATE_FORMAT_VERSION:
        raise BusinessProcessingError(f'Unsupported tracker template version {data["version"]}')
    return tracker_templates_create(data, user_id)


def tracker_templates_presets():
    return [{'key': key, 'name': preset['name'], 'description': preset['description']}
            for key, preset in _PRESETS.items()]


def tracker_templates_create_from_preset(preset, user_id):
    definition = _PRESETS.get(preset) if isinstance(preset, str) else None
    if definition is None:
        raise BusinessProcessingError(f'Unknown preset. Expected one of: {", ".join(TRACKER_TEMPLATE_PRESETS)}')
    return tracker_templates_create(definition, user_id)


def tracker_templates_apply(war_room_id, template_id, user_id):
    """Merge the template steps into the room (by name), create its
    trackers and add their widgets to the room's board (as many as fit),
    in one transaction. Trackers of a case source are skipped. Returns
    `(room steps, new trackers)`."""
    template = _get(template_id)
    steps, trackers = tracker_templates_validate_content({'steps': template.steps, 'trackers': template.trackers})
    trackers = [tracker for tracker in trackers if tracker['source'] in WAR_ROOM_TRACKER_SOURCES]
    room_steps = war_room_trackers_db_steps(war_room_id)
    room_trackers = war_room_trackers_db_trackers(war_room_id)
    by_name = {step.name.lower(): step for step in room_steps}
    new_steps = [step for step in steps if step['name'].lower() not in by_name]
    if len(room_steps) + len(new_steps) > WAR_ROOM_TRACKERS_MAX_STEPS:
        raise BusinessProcessingError(f'A war room has at most {WAR_ROOM_TRACKERS_MAX_STEPS} steps')
    if len(room_trackers) + len(trackers) > WAR_ROOM_TRACKERS_MAX_TRACKERS:
        raise BusinessProcessingError(f'A war room has at most {WAR_ROOM_TRACKERS_MAX_TRACKERS} trackers')

    sort_order = max((step.sort_order for step in room_steps), default=-1) + 1
    for step in new_steps:
        by_name[step['name'].lower()] = war_room_trackers_db_add(WarRoomTrackerStep(
            war_room_id=war_room_id, name=step['name'], description=step['description'], color=step['color'],
            icon=step['icon'], sort_order=sort_order,
        ))
        sort_order += 1

    room_step_ids = {step.id for step in by_name.values()}
    sort_order = max((tracker.sort_order for tracker in room_trackers), default=-1) + 1
    created = []
    widgets = []
    try:
        for definition in trackers:
            step_ids = None
            if definition['step_names'] is not None:
                step_ids = [by_name[name.lower()].id for name in definition['step_names']]
            tracker = war_room_trackers_build_tracker(war_room_id, {
                'name': definition['name'], 'icon': definition['icon'], 'description': definition['description'],
                'source': definition['source'], 'step_ids': step_ids, 'columns': definition['columns'],
                'show_asset_flags': definition['show_asset_flags'],
                'timeline_sync': definition['timeline_sync'],
            }, room_step_ids, sort_order, user_id)
            created.append(war_room_trackers_db_add(tracker))
            widgets.extend(_room_widget(widget, tracker.id, by_name) for widget in definition['board_widgets'])
            sort_order += 1
        war_room_board_widgets_append(war_room_id, widgets)
    except BusinessProcessingError:
        war_room_trackers_db_rollback()
        raise
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('The steps of this war room changed meanwhile, retry')
    track_activity(f'applied tracker template "{template.name}"', war_room_id=war_room_id)
    return war_room_trackers_db_steps(war_room_id), created


def tracker_templates_save_from_war_room(war_room_id, data, user_id):
    """A new template from the room's steps and trackers (all of them, or
    `tracker_ids`), with the widgets of the room's board showing them."""
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    name = _validate_name(data.get('name'))
    description = _text(data.get('description'), 'description', _DESCRIPTION_MAX)
    tracker_ids = data.get('tracker_ids')
    if tracker_ids is None:
        trackers = war_room_trackers_db_trackers(war_room_id)
    else:
        if not isinstance(tracker_ids, list) or not all(isinstance(i, int) and not isinstance(i, bool)
                                                        for i in tracker_ids):
            raise BusinessProcessingError('tracker_ids must be a list of integers')
        trackers = war_room_trackers_db_trackers_by_ids(war_room_id, tracker_ids)
        if len(trackers) != len(set(tracker_ids)):
            raise BusinessProcessingError('Unknown tracker in tracker_ids')
    room_steps = war_room_trackers_db_steps(war_room_id)
    step_names = {step.id: step.name for step in room_steps}
    room_widgets = war_room_board_widgets_stored(war_room_id) or []
    steps = [{'name': step.name, 'color': step.color, 'icon': step.icon, 'description': step.description}
             for step in room_steps]
    definitions = [{
        'name': tracker.name,
        'icon': tracker.icon,
        'description': tracker.description,
        'source': tracker.source,
        'step_names': [step_names[step_id] for step_id in tracker.step_ids if step_id in step_names]
        if tracker.step_ids is not None else None,
        'columns': tracker.columns or [],
        'show_asset_flags': bool(tracker.show_asset_flags),
        'timeline_sync': tracker.timeline_sync,
        'board_widgets': [_template_widget(widget, step_names) for widget in room_widgets
                          if widget.get('type') in TRACKER_WIDGET_TYPES
                          and (widget.get('config') or {}).get('tracker_id') == tracker.id][:_MAX_TRACKER_WIDGETS],
    } for tracker in trackers]
    steps, definitions = tracker_templates_validate_content({'steps': steps, 'trackers': definitions})
    return tracker_templates_serialize(_save_new(name, description, steps, definitions, user_id))


def tracker_templates_apply_to_case(case_id, template_id, user_id):
    """Merge the template steps into the case (by name) and create its
    trackers, in one transaction. Trackers of a war-room source and the
    board widgets are skipped. Returns `(case steps, new trackers, names of
    the skipped trackers)`."""
    template = _get(template_id)
    steps, definitions = tracker_templates_validate_content({'steps': template.steps,
                                                             'trackers': template.trackers})
    trackers = [tracker for tracker in definitions if tracker['source'] in CASE_TRACKER_SOURCES]
    skipped = [tracker['name'] for tracker in definitions if tracker['source'] not in CASE_TRACKER_SOURCES]
    case_steps = case_trackers_db_steps(case_id)
    case_trackers = case_trackers_db_trackers(case_id)
    by_name = {step.name.lower(): step for step in case_steps}
    new_steps = [step for step in steps if step['name'].lower() not in by_name]
    if len(case_steps) + len(new_steps) > CASE_TRACKERS_MAX_STEPS:
        raise BusinessProcessingError(f'A case has at most {CASE_TRACKERS_MAX_STEPS} steps')
    if len(case_trackers) + len(trackers) > CASE_TRACKERS_MAX_TRACKERS:
        raise BusinessProcessingError(f'A case has at most {CASE_TRACKERS_MAX_TRACKERS} trackers')

    sort_order = max((step.sort_order for step in case_steps), default=-1) + 1
    for step in new_steps:
        by_name[step['name'].lower()] = war_room_trackers_db_add(CaseTrackerStep(
            case_id=case_id, name=step['name'], description=step['description'], color=step['color'],
            icon=step['icon'], sort_order=sort_order,
        ))
        sort_order += 1

    case_step_ids = {step.id for step in by_name.values()}
    sort_order = max((tracker.sort_order for tracker in case_trackers), default=-1) + 1
    created = []
    try:
        for definition in trackers:
            step_ids = None
            if definition['step_names'] is not None:
                step_ids = [by_name[name.lower()].id for name in definition['step_names']]
            tracker = case_trackers_build_tracker(case_id, {
                'name': definition['name'], 'icon': definition['icon'], 'description': definition['description'],
                'source': definition['source'], 'step_ids': step_ids, 'columns': definition['columns'],
                'show_asset_flags': definition['show_asset_flags'],
                'timeline_sync': definition['timeline_sync'],
            }, case_step_ids, sort_order, user_id)
            created.append(war_room_trackers_db_add(tracker))
            sort_order += 1
    except BusinessProcessingError:
        war_room_trackers_db_rollback()
        raise
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('The steps of this case changed meanwhile, retry')
    track_activity(f'applied tracker template "{template.name}"', caseid=case_id)
    return case_trackers_db_steps(case_id), created, skipped


def tracker_templates_save_from_case(case_id, data, user_id):
    """A new template from the case's steps and trackers (all of them, or
    `tracker_ids`)."""
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    name = _validate_name(data.get('name'))
    description = _text(data.get('description'), 'description', _DESCRIPTION_MAX)
    trackers = case_trackers_db_trackers(case_id)
    tracker_ids = data.get('tracker_ids')
    if tracker_ids is not None:
        if not isinstance(tracker_ids, list) or not all(isinstance(i, int) and not isinstance(i, bool)
                                                        for i in tracker_ids):
            raise BusinessProcessingError('tracker_ids must be a list of integers')
        trackers = [tracker for tracker in trackers if tracker.id in set(tracker_ids)]
        if len(trackers) != len(set(tracker_ids)):
            raise BusinessProcessingError('Unknown tracker in tracker_ids')
    case_steps = case_trackers_db_steps(case_id)
    step_names = {step.id: step.name for step in case_steps}
    steps = [{'name': step.name, 'color': step.color, 'icon': step.icon, 'description': step.description}
             for step in case_steps]
    definitions = [{
        'name': tracker.name,
        'icon': tracker.icon,
        'description': tracker.description,
        'source': tracker.source,
        'step_names': [step_names[step_id] for step_id in tracker.step_ids if step_id in step_names]
        if tracker.step_ids is not None else None,
        'columns': tracker.columns or [],
        'show_asset_flags': bool(tracker.show_asset_flags),
        'timeline_sync': {'mode': tracker.timeline_sync['mode'], 'date': tracker.timeline_sync['date'],
                          'target': 'cases'} if tracker.timeline_sync else None,
        'board_widgets': [],
    } for tracker in trackers]
    steps, definitions = tracker_templates_validate_content({'steps': steps, 'trackers': definitions})
    return tracker_templates_serialize(_save_new(name, description, steps, definitions, user_id))
