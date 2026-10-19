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

"""v2 endpoints for admin-authored guided tutorials.

`/published` serves the tutorials catalogue to *any* authenticated user.
Every other route is admin-only (`Permissions.server_administrator`) and
backs Settings → Tutorials, where drafts are written and published.
"""

from flask import Blueprint
from flask import request
from marshmallow import ValidationError

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.tutorials import tutorials_create
from app.business.tutorials import tutorials_delete
from app.business.tutorials import tutorials_get
from app.business.tutorials import tutorials_list
from app.business.tutorials import tutorials_list_published
from app.business.tutorials import tutorials_update
from app.models.authorization import Permissions
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import TutorialSchema


_NO_STEPS_ERROR = {'steps': ['A published tutorial needs at least one step']}


class TutorialsOperations:

    def __init__(self):
        self._schema = TutorialSchema()

    def list_published(self):
        tutorials = tutorials_list_published()
        return response_api_success(self._schema.dump(tutorials, many=True))

    def list_all(self):
        tutorials = tutorials_list()
        return response_api_success(self._schema.dump(tutorials, many=True))

    def create(self):
        try:
            request_data = request.get_json() or {}
            tutorial = self._schema.load(request_data)
            tutorials_create(tutorial, iris_current_user)
            return response_api_created(self._schema.dump(tutorial))
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

    def read(self, identifier):
        try:
            tutorial = tutorials_get(identifier)
            return response_api_success(self._schema.dump(tutorial))
        except ObjectNotFoundError:
            return response_api_not_found()

    def update(self, identifier):
        try:
            tutorial = tutorials_get(identifier)
            request_data = request.get_json() or {}
            # The schema can only see the fields that were sent; publishing
            # an existing empty draft must be caught against the row.
            published = request_data.get('published', tutorial.published)
            steps = request_data.get('steps', tutorial.steps)
            if published is True and not steps:
                return response_api_error('Data error', data=_NO_STEPS_ERROR)
            self._schema.load(request_data, instance=tutorial, partial=True)
            tutorials_update(tutorial)
            return response_api_success(self._schema.dump(tutorial))
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)
        except ObjectNotFoundError:
            return response_api_not_found()

    @staticmethod
    def delete(identifier):
        try:
            tutorial = tutorials_get(identifier)
            tutorials_delete(tutorial)
            return response_api_deleted()
        except ObjectNotFoundError:
            return response_api_not_found()


tutorials_blueprint = Blueprint('tutorials_rest_v2', __name__, url_prefix='/tutorials')

tutorials_operations = TutorialsOperations()


@tutorials_blueprint.get('/published')
@ac_api_requires()
@api_doc(response=TutorialSchema, tags=['ManageTutorials'],
         summary='List published tutorials (any authenticated user)')
def list_published_tutorials_route():
    return tutorials_operations.list_published()


@tutorials_blueprint.get('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response=TutorialSchema, tags=['ManageTutorials'],
         summary='List all tutorials, drafts included')
def list_tutorials_route():
    return tutorials_operations.list_all()


@tutorials_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(request=TutorialSchema, response=TutorialSchema, response_shape='created',
         tags=['ManageTutorials'], summary='Create a tutorial')
def create_tutorial_route():
    return tutorials_operations.create()


@tutorials_blueprint.get('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response=TutorialSchema, tags=['ManageTutorials'],
         summary='Get a tutorial')
def get_tutorial_route(identifier):
    return tutorials_operations.read(identifier)


@tutorials_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(request=TutorialSchema, response=TutorialSchema,
         tags=['ManageTutorials'], summary='Update a tutorial')
def put_tutorial_route(identifier):
    return tutorials_operations.update(identifier)


@tutorials_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['ManageTutorials'],
         summary='Delete a tutorial')
def delete_tutorial_route(identifier):
    return tutorials_operations.delete(identifier)
