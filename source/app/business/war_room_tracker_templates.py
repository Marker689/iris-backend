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

Built-in presets are not seeded: an administrator creates a template
from one through `tracker_templates_create_from_preset`.
"""

import datetime

from app.business.war_room_trackers import WAR_ROOM_TRACKERS_MAX_STEPS
from app.business.war_room_trackers import WAR_ROOM_TRACKERS_MAX_TRACKERS
from app.business.war_room_trackers import war_room_trackers_build_tracker
from app.business.war_room_trackers import war_room_trackers_validate_color
from app.business.war_room_trackers import war_room_trackers_validate_columns
from app.business.war_room_trackers import war_room_trackers_validate_icon
from app.business.war_room_trackers import war_room_trackers_validate_source
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
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import TrackerTemplate
from app.models.war_rooms import WarRoomTrackerStep


TRACKER_TEMPLATE_FORMAT = 'iris-tracker-template'
TRACKER_TEMPLATE_FORMAT_VERSION = 1

_NAME_MAX = 128
_STEP_NAME_MAX = 64
_DESCRIPTION_MAX = 4000
_MAX_TEMPLATE_TRACKERS = 20

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
             'columns': [{'label': 'Contact', 'type': 'text'}, {'label': 'Notified on', 'type': 'date'}]},
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
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                raise BusinessProcessingError('step_names must be a list of step names')
            resolved = []
            for name in names:
                match = known.get(name.strip().lower())
                if match is None:
                    raise BusinessProcessingError(f'Unknown step "{name}" in tracker step_names')
                if match not in resolved:
                    resolved.append(match)
            names = resolved
        show_asset_flags = tracker.get('show_asset_flags', False)
        if not isinstance(show_asset_flags, bool):
            raise BusinessProcessingError('show_asset_flags must be a boolean')
        out.append({
            'name': _text(tracker.get('name'), 'Tracker name', _NAME_MAX, required=True),
            'icon': war_room_trackers_validate_icon(tracker.get('icon')),
            'description': _text(tracker.get('description'), 'description', _DESCRIPTION_MAX),
            'source': war_room_trackers_validate_source(tracker.get('source')),
            'step_names': names,
            'columns': war_room_trackers_validate_columns(tracker.get('columns')),
            'show_asset_flags': show_asset_flags,
        })
    return out


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
    """Merge the template steps into the room (by name) and create its
    trackers, in one transaction. Returns `(room steps, new trackers)`."""
    template = _get(template_id)
    steps, trackers = tracker_templates_validate_content({'steps': template.steps, 'trackers': template.trackers})
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
    try:
        for definition in trackers:
            step_ids = None
            if definition['step_names'] is not None:
                step_ids = [by_name[name.lower()].id for name in definition['step_names']]
            tracker = war_room_trackers_build_tracker(war_room_id, {
                'name': definition['name'], 'icon': definition['icon'], 'description': definition['description'],
                'source': definition['source'], 'step_ids': step_ids, 'columns': definition['columns'],
                'show_asset_flags': definition['show_asset_flags'],
            }, room_step_ids, sort_order, user_id)
            created.append(war_room_trackers_db_add(tracker))
            sort_order += 1
    except BusinessProcessingError:
        war_room_trackers_db_rollback()
        raise
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('The steps of this war room changed meanwhile, retry')
    track_activity(f'applied tracker template "{template.name}"', war_room_id=war_room_id)
    return war_room_trackers_db_steps(war_room_id), created


def tracker_templates_save_from_war_room(war_room_id, data, user_id):
    """A new template from the room's steps and trackers (all of them, or
    `tracker_ids`)."""
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
    } for tracker in trackers]
    steps, definitions = tracker_templates_validate_content({'steps': steps, 'trackers': definitions})
    return tracker_templates_serialize(_save_new(name, description, steps, definitions, user_id))
