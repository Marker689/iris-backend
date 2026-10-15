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


"""War-room categories (mounted at `/api/v2/war-rooms/categories`).

    GET    /           list the categories       [war_rooms_read]
    POST   /           create a category         [war_rooms_write]
    PATCH  /<id>       rename a category         [war_rooms_write]
    DELETE /<id>       delete a category, its rooms become uncategorised
                                                 [war_rooms_write]

Moving a room is `PUT /api/v2/war-rooms/<id>/category`, authorised on
the room. The categories carry no room count: the caller counts the
rooms it can see.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.categories import categories_create
from app.business.categories import categories_delete
from app.business.categories import categories_list
from app.business.categories import categories_rename
from app.business.categories import categories_serialize
from app.models.authorization import Permissions
from app.models.categories import CATEGORY_KIND_WAR_ROOM
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_categories_blueprint = Blueprint(
    'war_rooms_categories_rest_v2', __name__, url_prefix='/categories'
)


def _has(permission):
    return ac_current_user_has_permission(permission) \
        or ac_current_user_has_permission(Permissions.server_administrator)


def _can_read():
    return _has(Permissions.war_rooms_read) or _has(Permissions.war_rooms_write)


@war_rooms_categories_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRooms'], summary='List the war room categories')
def list_war_room_categories():
    if not _can_read():
        return ac_api_return_access_denied()
    categories = categories_list(CATEGORY_KIND_WAR_ROOM)
    return response_api_success(data=[categories_serialize(c) for c in categories])


@war_rooms_categories_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRooms'], summary='Create a war room category')
def create_war_room_category():
    if not _has(Permissions.war_rooms_write):
        return ac_api_return_access_denied()
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        category = categories_create(CATEGORY_KIND_WAR_ROOM, raw.get('name'), iris_current_user.id)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_created(categories_serialize(category))


@war_rooms_categories_blueprint.patch('/<int:category_id>')
@ac_api_requires()
@api_doc(tags=['WarRooms'], summary='Rename a war room category')
def rename_war_room_category(category_id):
    if not _has(Permissions.war_rooms_write):
        return ac_api_return_access_denied()
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        category = categories_rename(CATEGORY_KIND_WAR_ROOM, category_id, raw.get('name'))
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(data=categories_serialize(category))


@war_rooms_categories_blueprint.delete('/<int:category_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRooms'],
         summary='Delete a war room category (its war rooms become uncategorised)')
def delete_war_room_category(category_id):
    if not _has(Permissions.war_rooms_write):
        return ac_api_return_access_denied()
    try:
        categories_delete(CATEGORY_KIND_WAR_ROOM, category_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_deleted()
