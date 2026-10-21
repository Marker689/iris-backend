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

"""v2 endpoints for the war-room tracker templates. Reading needs
war_rooms_read (to pick one to apply) or server administrator; changes,
import / export and the presets need server administrator."""

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
from app.business.war_room_tracker_templates import tracker_templates_create
from app.business.war_room_tracker_templates import tracker_templates_create_from_preset
from app.business.war_room_tracker_templates import tracker_templates_delete
from app.business.war_room_tracker_templates import tracker_templates_export
from app.business.war_room_tracker_templates import tracker_templates_get
from app.business.war_room_tracker_templates import tracker_templates_import
from app.business.war_room_tracker_templates import tracker_templates_list
from app.business.war_room_tracker_templates import tracker_templates_presets
from app.business.war_room_tracker_templates import tracker_templates_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


tracker_templates_blueprint = Blueprint(
    'tracker_templates_rest_v2', __name__,
    url_prefix='/tracker-templates'
)


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


def _can_read():
    return ac_current_user_has_permission(Permissions.war_rooms_read) \
        or ac_current_user_has_permission(Permissions.server_administrator)


@tracker_templates_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['TrackerTemplates'], summary='List the tracker templates')
def list_tracker_templates_route():
    if not _can_read():
        return ac_api_return_access_denied()
    return _handle(tracker_templates_list)


@tracker_templates_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['TrackerTemplates'], summary='Create a tracker template')
def create_tracker_template_route():
    body = _body()
    return _handle(lambda: tracker_templates_create(body, iris_current_user.id), created=True)


@tracker_templates_blueprint.post('/import')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['TrackerTemplates'], summary='Import a tracker template exported as JSON')
def import_tracker_template_route():
    body = _body()
    return _handle(lambda: tracker_templates_import(body, iris_current_user.id), created=True)


@tracker_templates_blueprint.get('/presets')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['TrackerTemplates'], summary='List the built-in tracker template presets')
def list_tracker_template_presets_route():
    return response_api_success(tracker_templates_presets())


@tracker_templates_blueprint.post('/presets/<string:preset>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['TrackerTemplates'], summary='Create a tracker template from a built-in preset')
def create_tracker_template_from_preset_route(preset):
    return _handle(lambda: tracker_templates_create_from_preset(preset, iris_current_user.id), created=True)


@tracker_templates_blueprint.get('/<int:identifier>')
@ac_api_requires()
@api_doc(tags=['TrackerTemplates'], summary='Get a tracker template')
def get_tracker_template_route(identifier):
    if not _can_read():
        return ac_api_return_access_denied()
    return _handle(lambda: tracker_templates_get(identifier))


@tracker_templates_blueprint.get('/<int:identifier>/export')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['TrackerTemplates'], summary='Export a tracker template as JSON (re-importable)')
def export_tracker_template_route(identifier):
    return _handle(lambda: tracker_templates_export(identifier))


@tracker_templates_blueprint.patch('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['TrackerTemplates'], summary='Update a tracker template (fields left out keep their value)')
def update_tracker_template_route(identifier):
    body = _body()
    return _handle(lambda: tracker_templates_update(identifier, body))


@tracker_templates_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['TrackerTemplates'], summary='Delete a tracker template')
def delete_tracker_template_route(identifier):
    try:
        tracker_templates_delete(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()
