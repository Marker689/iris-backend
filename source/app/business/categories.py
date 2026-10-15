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


"""Categories of war rooms and AI workflows.

The REST layer checks the permission of the kind (`war_rooms_write`,
`ai_workflows_write`) before any change; moving an object into a
category is authorised on the object itself (see `war_room_set_category`
and `ai_workflows_set_category`). A category id always resolves within
its kind, so a war-room category can never file a workflow.
"""

from app.datamgmt.categories.categories_db import categories_db_delete
from app.datamgmt.categories.categories_db import categories_db_get
from app.datamgmt.categories.categories_db import categories_db_list
from app.datamgmt.categories.categories_db import categories_db_name_taken
from app.datamgmt.categories.categories_db import categories_db_save
from app.iris_engine.utils.tracker import track_activity
from app.models.categories import CATEGORY_KIND_AI_WORKFLOW
from app.models.categories import CATEGORY_KIND_WAR_ROOM
from app.models.categories import CATEGORY_NAME_MAX_LEN
from app.models.categories import ObjectCategory
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_KIND_LABELS = {
    CATEGORY_KIND_WAR_ROOM: 'war room',
    CATEGORY_KIND_AI_WORKFLOW: 'AI workflow',
}


def _check_kind(kind):
    if kind not in _KIND_LABELS:
        raise BusinessProcessingError(f'Unknown category kind {kind}')


def _validate_name(kind, name, exclude_id=None):
    if not isinstance(name, str) or not name.strip():
        raise BusinessProcessingError('Category name is required')
    name = ' '.join(name.split())
    if len(name) > CATEGORY_NAME_MAX_LEN:
        raise BusinessProcessingError(f'Category name must be at most {CATEGORY_NAME_MAX_LEN} characters')
    if categories_db_name_taken(kind, name, exclude_id=exclude_id):
        raise BusinessProcessingError(f'A category named "{name}" already exists')
    return name


def _save(category):
    if not categories_db_save(category):
        raise BusinessProcessingError(f'A category named "{category.name}" already exists')


def categories_serialize(category):
    return {
        'id': category.id,
        'name': category.name,
        'created_at': category.created_at.isoformat() if category.created_at else None,
        'created_by_id': category.created_by_id,
    }


def categories_list(kind):
    _check_kind(kind)
    return categories_db_list(kind)


def categories_get(kind, category_id):
    _check_kind(kind)
    category = categories_db_get(kind, category_id)
    if category is None:
        raise ObjectNotFoundError()
    return category


def categories_resolve_id(kind, category_id):
    """The category id an object may be filed under: None (no category)
    or the id of an existing category of `kind`."""
    if category_id is None:
        return None
    if isinstance(category_id, bool) or not isinstance(category_id, int):
        raise BusinessProcessingError('category_id must be an integer or null')
    _check_kind(kind)
    if categories_db_get(kind, category_id) is None:
        raise BusinessProcessingError('Category not found')
    return category_id


def categories_create(kind, name, user_id):
    _check_kind(kind)
    category = ObjectCategory()
    category.kind = kind
    category.name = _validate_name(kind, name)
    category.created_by_id = user_id
    _save(category)
    track_activity(f'created {_KIND_LABELS[kind]} category "{category.name}"', ctx_less=True)
    return category


def categories_rename(kind, category_id, name):
    category = categories_get(kind, category_id)
    previous = category.name
    category.name = _validate_name(kind, name, exclude_id=category.id)
    if category.name != previous:
        _save(category)
        track_activity(f'renamed {_KIND_LABELS[kind]} category "{previous}" to "{category.name}"', ctx_less=True)
    return category


def categories_delete(kind, category_id):
    category = categories_get(kind, category_id)
    name = category.name
    categories_db_delete(category)
    track_activity(f'deleted {_KIND_LABELS[kind]} category "{name}"', ctx_less=True)
