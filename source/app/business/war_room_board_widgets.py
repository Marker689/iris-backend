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

"""War-room board widgets: what the board of a room shows, set by the
room's writers (`war_room.board`, NULL = `war_room_board_widgets_default`).
Each user can still hide and reorder the widgets for themselves, on
their side (a user preference).

A widget is `{id, type, title, column, config}`:
  * built-in types (`BUILTIN_TYPES`) are the fixed parts of the board
    (KPI tiles, flags, cases, decisions, ...). Each appears at most once
    and its id is its type. Only `cases` has a config: the steps whose
    tracker rows are highlighted on the case cards (`highlight_step_ids`)
    and the trackers looked at (`tracker_ids`, null = all of them).
  * configurable types can appear several times, under any other id:
      - `tracker_progress`: rows of a tracker per step, as bars, a donut
        or a funnel (`tracker_id`, `step_ids` null = the steps the
        tracker offers, `display`, `filter`)
      - `tracker_count`: a tile counting the rows of a tracker carrying
        one of `step_ids` (all its rows when empty), out of all its rows
        with `of_total` (`tracker_id`, `step_ids`, `of_total`, `filter`)
      - `tracker_list`: the rows of a tracker carrying one of `step_ids`
        (all its rows when empty) (`tracker_id`, `step_ids`, `filter`)
      - `tracker_breakdown`: rows of a tracker per value of a select,
        multiselect or checkbox column and per step (`tracker_id`,
        `column_key`, `step_ids` null = the offered steps)
      - `tasks`: open war-room tasks (`in_progress_only`, `team_ids`)
      - `note`: a markdown note (`text`)
    A `filter` keeps the rows whose `column_key` value is one of
    `values` (any of them for a multiselect).

Panels sit in the `main` or the `side` column; tiles and the banner have
no column. Deleting a tracker, a step or a tracker column prunes the
widgets referencing it (`war_room_board_widgets_prune`), deleting a team
drops it from the `tasks` widgets."""

import re
import secrets

from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_get_board
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_set_board
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_team_ids
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_steps
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_trackers
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


WAR_ROOM_BOARD_MAX_WIDGETS = 40
WAR_ROOM_BOARD_LIST_ROWS = 50

ZONE_BANNER = 'banner'
ZONE_TILE = 'tile'
ZONE_PANEL = 'panel'
COLUMNS = ('main', 'side')

# type -> (zone, default column)
BUILTIN_TYPES = {
    'ai_suggestions': (ZONE_BANNER, None),
    'kpi_compromised': (ZONE_TILE, None),
    'kpi_decisions': (ZONE_TILE, None),
    'kpi_exceptions': (ZONE_TILE, None),
    'kpi_tasks': (ZONE_TILE, None),
    'flags': (ZONE_TILE, None),
    'vuln_open': (ZONE_TILE, None),
    'vuln_exploited': (ZONE_TILE, None),
    'vuln_overdue': (ZONE_TILE, None),
    'vuln_kev': (ZONE_TILE, None),
    'vuln_assets': (ZONE_TILE, None),
    'cases': (ZONE_PANEL, 'main'),
    'decisions': (ZONE_PANEL, 'side'),
    'attention': (ZONE_PANEL, 'side'),
}
CONFIGURABLE_TYPES = {
    'tracker_progress': (ZONE_PANEL, 'main'),
    'tracker_count': (ZONE_TILE, None),
    'tracker_list': (ZONE_PANEL, 'main'),
    'tracker_breakdown': (ZONE_PANEL, 'main'),
    'tasks': (ZONE_PANEL, 'side'),
    'note': (ZONE_PANEL, 'side'),
}
WIDGET_TYPES = {**BUILTIN_TYPES, **CONFIGURABLE_TYPES}
TRACKER_WIDGET_TYPES = ('tracker_progress', 'tracker_count', 'tracker_list', 'tracker_breakdown')
PROGRESS_DISPLAYS = ('bars', 'donut', 'funnel')
BREAKDOWN_COLUMN_TYPES = ('select', 'multiselect', 'checkbox')

_ID_RE = re.compile(r'^[a-z0-9_-]{1,40}$')
_TITLE_MAX = 128
_NOTE_MAX = 20000
_MAX_IDS = 50
_MAX_FILTER_VALUES = 50
_FILTER_VALUE_MAX = 256


def _widget(widget_id, widget_type, config=None):
    zone, column = WIDGET_TYPES[widget_type]
    return {'id': widget_id, 'type': widget_type, 'title': None,
            'column': column if zone == ZONE_PANEL else None, 'config': config or {}}


def war_room_board_widgets_default():
    """The board of a room whose writers never changed it."""
    return [
        _widget('ai_suggestions', 'ai_suggestions'),
        _widget('kpi_compromised', 'kpi_compromised'),
        _widget('kpi_decisions', 'kpi_decisions'),
        _widget('kpi_exceptions', 'kpi_exceptions'),
        _widget('kpi_tasks', 'kpi_tasks'),
        _widget('flags', 'flags'),
        _widget('vuln_open', 'vuln_open'),
        _widget('vuln_exploited', 'vuln_exploited'),
        _widget('vuln_overdue', 'vuln_overdue'),
        _widget('vuln_kev', 'vuln_kev'),
        _widget('vuln_assets', 'vuln_assets'),
        _widget('cases', 'cases', {'highlight_step_ids': [], 'tracker_ids': None}),
        _widget('decisions', 'decisions'),
        _widget('attention', 'attention'),
        _widget('tasks', 'tasks', {'in_progress_only': False, 'team_ids': []}),
    ]


def war_room_board_widgets_new_id():
    return f'w_{secrets.token_hex(4)}'


# ----------------------------------------------------------- Validation --

class WarRoomBoardContext:
    """What a board is validated against: the room's trackers
    (`{tracker id: columns}`), steps and teams."""

    def __init__(self, tracker_columns, step_ids, team_ids):
        self.tracker_columns = tracker_columns
        self.step_ids = set(step_ids)
        self.team_ids = set(team_ids)

    @classmethod
    def of_room(cls, war_room_id):
        return cls({tracker.id: tracker.columns or [] for tracker in war_room_trackers_db_trackers(war_room_id)},
                   [step.id for step in war_room_trackers_db_steps(war_room_id)],
                   war_room_board_db_team_ids(war_room_id))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _ids(value, field, known, what):
    """De-duplicated ids, order kept, each one in `known`."""
    if not isinstance(value, list) or not all(_is_int(item) for item in value):
        raise BusinessProcessingError(f'{field} must be a list of integers')
    out = []
    for item in value:
        if item not in known:
            raise BusinessProcessingError(f'{field}: unknown {what} #{item}')
        if item not in out:
            out.append(item)
    if len(out) > _MAX_IDS:
        raise BusinessProcessingError(f'{field} accepts at most {_MAX_IDS} entries')
    return out


def _optional_ids(value, field, known, what):
    return None if value is None else _ids(value, field, known, what)


def _bool(value, field, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def _text(value, field, max_len):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be a string')
    value = value.strip()
    if len(value) > max_len:
        raise BusinessProcessingError(f'{field} must be at most {max_len} characters')
    return value


def _tracker_id(config, ctx, label):
    tracker_id = config.get('tracker_id')
    if not _is_int(tracker_id) or tracker_id not in ctx.tracker_columns:
        raise BusinessProcessingError(f'{label}: unknown tracker')
    return tracker_id


def _column(ctx, tracker_id, key):
    return next((column for column in ctx.tracker_columns[tracker_id] if column['key'] == key), None)


def _filter_value(value, label):
    if isinstance(value, bool) or _is_int(value):
        return value
    if isinstance(value, str) and value.strip() and len(value) <= _FILTER_VALUE_MAX:
        return value
    raise BusinessProcessingError(f'{label}: filter values must be strings, integers or booleans')


def _filter(value, ctx, tracker_id, label):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BusinessProcessingError(f'{label}: filter must be an object or null')
    key = value.get('column_key')
    if not isinstance(key, str) or _column(ctx, tracker_id, key) is None:
        raise BusinessProcessingError(f'{label}: the filter column is not a column of the tracker')
    values = value.get('values')
    if not isinstance(values, list) or not values or len(values) > _MAX_FILTER_VALUES:
        raise BusinessProcessingError(f'{label}: filter values must be a list of 1 to {_MAX_FILTER_VALUES} values')
    out = []
    for item in values:
        item = _filter_value(item, label)
        if item not in out:
            out.append(item)
    return {'column_key': key, 'values': out}


def _config_cases(config, ctx, label):
    return {
        'highlight_step_ids': _ids(config.get('highlight_step_ids') or [], f'{label}: highlight_step_ids',
                                   ctx.step_ids, 'step'),
        'tracker_ids': _optional_ids(config.get('tracker_ids'), f'{label}: tracker_ids', ctx.tracker_columns,
                                     'tracker'),
    }


def _config_tracker_progress(config, ctx, label):
    tracker_id = _tracker_id(config, ctx, label)
    display = config.get('display') or 'bars'
    if display not in PROGRESS_DISPLAYS:
        raise BusinessProcessingError(f'{label}: display must be one of: {", ".join(PROGRESS_DISPLAYS)}')
    return {
        'tracker_id': tracker_id,
        'step_ids': _optional_ids(config.get('step_ids'), f'{label}: step_ids', ctx.step_ids, 'step'),
        'display': display,
        'filter': _filter(config.get('filter'), ctx, tracker_id, label),
    }


def _config_tracker_count(config, ctx, label):
    tracker_id = _tracker_id(config, ctx, label)
    return {
        'tracker_id': tracker_id,
        'step_ids': _ids(config.get('step_ids') or [], f'{label}: step_ids', ctx.step_ids, 'step'),
        'of_total': _bool(config.get('of_total'), f'{label}: of_total', default=True),
        'filter': _filter(config.get('filter'), ctx, tracker_id, label),
    }


def _config_tracker_list(config, ctx, label):
    tracker_id = _tracker_id(config, ctx, label)
    return {
        'tracker_id': tracker_id,
        'step_ids': _ids(config.get('step_ids') or [], f'{label}: step_ids', ctx.step_ids, 'step'),
        'filter': _filter(config.get('filter'), ctx, tracker_id, label),
    }


def _config_tracker_breakdown(config, ctx, label):
    tracker_id = _tracker_id(config, ctx, label)
    key = config.get('column_key')
    column = _column(ctx, tracker_id, key) if isinstance(key, str) else None
    if column is None or column['type'] not in BREAKDOWN_COLUMN_TYPES:
        raise BusinessProcessingError(f'{label}: column_key must be a select, multiselect or checkbox column '
                                      f'of the tracker')
    return {
        'tracker_id': tracker_id,
        'column_key': key,
        'step_ids': _optional_ids(config.get('step_ids'), f'{label}: step_ids', ctx.step_ids, 'step'),
    }


def _config_tasks(config, ctx, label):
    return {
        'in_progress_only': _bool(config.get('in_progress_only'), f'{label}: in_progress_only'),
        'team_ids': _ids(config.get('team_ids') or [], f'{label}: team_ids', ctx.team_ids, 'team'),
    }


def _config_note(config, _ctx, label):
    return {'text': _text(config.get('text'), f'{label}: text', _NOTE_MAX) or ''}


_CONFIG_VALIDATORS = {
    'cases': _config_cases,
    'tracker_progress': _config_tracker_progress,
    'tracker_count': _config_tracker_count,
    'tracker_list': _config_tracker_list,
    'tracker_breakdown': _config_tracker_breakdown,
    'tasks': _config_tasks,
    'note': _config_note,
}


def _validate_widget(value, index, ctx):
    label = f'Widget #{index + 1}'
    if not isinstance(value, dict):
        raise BusinessProcessingError(f'{label} must be an object')
    widget_type = value.get('type')
    if widget_type not in WIDGET_TYPES:
        raise BusinessProcessingError(f'{label}: unknown type')
    widget_id = value.get('id')
    if widget_type in BUILTIN_TYPES:
        if widget_id is not None and widget_id != widget_type:
            raise BusinessProcessingError(f'{label}: the id of a {widget_type} widget is its type')
        widget_id = widget_type
    elif widget_id is None:
        widget_id = war_room_board_widgets_new_id()
    elif not isinstance(widget_id, str) or not _ID_RE.fullmatch(widget_id) or widget_id in BUILTIN_TYPES:
        raise BusinessProcessingError(f'{label}: id must match [a-z0-9_-]{{1,40}} and not be a built-in type')
    zone, default_column = WIDGET_TYPES[widget_type]
    column = None
    if zone == ZONE_PANEL:
        column = value.get('column') or default_column
        if column not in COLUMNS:
            raise BusinessProcessingError(f'{label}: column must be one of: {", ".join(COLUMNS)}')
    config = value.get('config')
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise BusinessProcessingError(f'{label}: config must be an object')
    validator = _CONFIG_VALIDATORS.get(widget_type)
    return {
        'id': widget_id,
        'type': widget_type,
        'title': _text(value.get('title'), f'{label}: title', _TITLE_MAX),
        'column': column,
        'config': validator(config, ctx, label) if validator is not None else {},
    }


def war_room_board_widgets_validate(value, ctx):
    """The widgets of a board, normalised. Ids are unique."""
    if not isinstance(value, list):
        raise BusinessProcessingError('widgets must be a list')
    if len(value) > WAR_ROOM_BOARD_MAX_WIDGETS:
        raise BusinessProcessingError(f'A board has at most {WAR_ROOM_BOARD_MAX_WIDGETS} widgets')
    out = []
    seen = set()
    for index, item in enumerate(value):
        widget = _validate_widget(item, index, ctx)
        if widget['id'] in seen:
            raise BusinessProcessingError(f'Widget #{index + 1}: duplicated id {widget["id"]}')
        seen.add(widget['id'])
        out.append(widget)
    return out


# ---------------------------------------------------------- Read, write --

def war_room_board_widgets_stored(war_room_id):
    """The widgets the room's writers set, or None (the default board)."""
    board = war_room_board_db_get_board(war_room_id)
    if not isinstance(board, dict) or not isinstance(board.get('widgets'), list):
        return None
    return board['widgets']


def war_room_board_widgets_get(war_room_id):
    """`(widgets, customized)`."""
    widgets = war_room_board_widgets_stored(war_room_id)
    if widgets is None:
        return war_room_board_widgets_default(), False
    return widgets, True


def war_room_board_widgets_set(war_room_id, data):
    """Replace the room's widgets (`{widgets}`, null = back to the default
    board). Returns `(widgets, customized)`."""
    if not isinstance(data, dict) or 'widgets' not in data:
        raise BusinessProcessingError('Body must contain a `widgets` field (a list, or null for the default board)')
    if data['widgets'] is None:
        board = None
    else:
        board = {'widgets': war_room_board_widgets_validate(data['widgets'], WarRoomBoardContext.of_room(war_room_id))}
    if not war_room_board_db_set_board(war_room_id, board):
        raise ObjectNotFoundError()
    track_activity('reset the war-room board' if board is None else 'updated the war-room board',
                   war_room_id=war_room_id)
    return war_room_board_widgets_get(war_room_id)


def war_room_board_widgets_append(war_room_id, widgets):
    """Add widgets at the end of the room's board (the default board
    first when the room has none), without committing. Widgets that do
    not fit any more are dropped; returns how many were added."""
    if not widgets:
        return 0
    current = war_room_board_widgets_stored(war_room_id)
    if current is None:
        current = war_room_board_widgets_default()
    added = widgets[:max(WAR_ROOM_BOARD_MAX_WIDGETS - len(current), 0)]
    merged = war_room_board_widgets_validate(current + added, WarRoomBoardContext.of_room(war_room_id))
    war_room_board_db_set_board(war_room_id, {'widgets': merged}, commit=False)
    return len(added)


# -------------------------------------------------------------- Pruning --

def _prune_widget(widget, tracker_id, step_id, tracker_columns, team_id):
    """The widget without the references to a deleted tracker, step,
    column or team, or None when it is left without its tracker or
    column."""
    config = dict(widget.get('config') or {})
    if team_id is not None and isinstance(config.get('team_ids'), list):
        config['team_ids'] = [i for i in config['team_ids'] if i != team_id]
    if tracker_id is not None:
        if config.get('tracker_id') == tracker_id:
            return None
        if isinstance(config.get('tracker_ids'), list):
            config['tracker_ids'] = [i for i in config['tracker_ids'] if i != tracker_id]
    if step_id is not None:
        for key in ('step_ids', 'highlight_step_ids'):
            if isinstance(config.get(key), list):
                config[key] = [i for i in config[key] if i != step_id]
    if tracker_columns is not None and config.get('tracker_id') == tracker_columns[0]:
        keys = {column['key'] for column in tracker_columns[1]}
        if widget.get('type') == 'tracker_breakdown' and config.get('column_key') not in keys:
            return None
        if isinstance(config.get('filter'), dict) and config['filter'].get('column_key') not in keys:
            config['filter'] = None
    return {**widget, 'config': config}


def war_room_board_widgets_prune(war_room_id, tracker_id=None, step_id=None, tracker_columns=None, team_id=None):
    """Drop from the room's board the references to a deleted tracker,
    step or team, or to the columns a tracker no longer has
    (`tracker_columns` is `(tracker id, its columns)`), without
    committing."""
    widgets = war_room_board_widgets_stored(war_room_id)
    if widgets is None:
        return
    pruned = []
    for widget in widgets:
        widget = _prune_widget(widget, tracker_id, step_id, tracker_columns, team_id)
        if widget is not None:
            pruned.append(widget)
    if pruned != widgets:
        war_room_board_db_set_board(war_room_id, {'widgets': pruned}, commit=False)
