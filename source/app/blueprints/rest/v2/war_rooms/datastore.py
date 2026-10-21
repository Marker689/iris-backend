#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""War-room datastore REST routes.

Files, plus the folder tree mirroring the case datastore endpoints
(`/tree`, `/folders`, `/folders/<id>/rename|move`, `/<file_id>/move`),
with the same `parent_node` / `folder_name` / `destination_node` bodies.
"""

from flask import Blueprint, request, send_file

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.business.war_room_datastore import war_room_attached_cases
from app.business.war_room_datastore import war_room_datastore_add_folder
from app.business.war_room_datastore import war_room_datastore_delete
from app.business.war_room_datastore import war_room_datastore_delete_folder
from app.business.war_room_datastore import war_room_datastore_get
from app.business.war_room_datastore import war_room_datastore_get_folder
from app.business.war_room_datastore import war_room_datastore_list
from app.business.war_room_datastore import war_room_datastore_list_folders
from app.business.war_room_datastore import war_room_datastore_move
from app.business.war_room_datastore import war_room_datastore_move_folder
from app.business.war_room_datastore import war_room_datastore_open
from app.business.war_room_datastore import war_room_datastore_rename_folder
from app.business.war_room_datastore import war_room_datastore_save
from app.business.war_room_datastore import war_room_datastore_tree
from app.business.war_room_datastore import war_room_datastore_update
from app.iris_engine.demo_builder import demo_mode_over_upload_cap
from app.iris_engine.demo_builder import demo_mode_upload_cap_message
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_datastore_blueprint = Blueprint(
    'war_rooms_datastore_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/datastore'
)


def _serialize(row):
    return {
        'file_id': row.file_id,
        'war_room_id': row.war_room_id,
        'filename': row.filename,
        'description': row.description,
        'size_bytes': row.size_bytes,
        'mime_type': row.mime_type,
        'sha256': row.sha256,
        'uploaded_at': row.uploaded_at.isoformat() if row.uploaded_at else None,
        'uploaded_by_id': row.uploaded_by_id,
        'tags': row.tags,
        'file_parent_id': row.file_parent_id,
    }


def _serialize_folder(folder):
    return {
        'path_id': folder.path_id,
        'path_name': folder.path_name,
        'path_parent_id': folder.path_parent_id,
        'path_is_root': folder.path_is_root,
        'war_room_id': folder.war_room_id,
    }


def _as_int(value):
    """`value` as an int, None when it isn't one."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _upload(war_room_id, folder_id):
    if 'file' not in request.files:
        return response_api_error('A "file" multipart field is required')
    file_storage = request.files['file']
    if not file_storage.filename:
        return response_api_error('Empty filename')

    if demo_mode_over_upload_cap(file_storage):
        return response_api_error(demo_mode_upload_cap_message())

    try:
        row = war_room_datastore_save(
            war_room_id,
            file_stream=file_storage.stream,
            filename=request.form.get('filename') or file_storage.filename,
            mime_type=file_storage.mimetype,
            description=request.form.get('description'),
            tags=request.form.get('tags'),
            uploaded_by_id=iris_current_user.id,
            folder_id=folder_id,
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    return response_api_created(_serialize(row))


@war_rooms_datastore_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='List datastore files')
def list_files(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    rows = war_room_datastore_list(war_room_id)
    attached = war_room_attached_cases(war_room_id)
    return response_api_success({
        'files': [_serialize(r) for r in rows],
        'attached_case_ids': attached,
    })


@war_rooms_datastore_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomDatastore'], summary='Upload a datastore file')
def upload_file(war_room_id):
    """Uploads to the root folder; `/folders/<id>/files` targets another."""
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    return _upload(war_room_id, None)


@war_rooms_datastore_blueprint.get('/tree')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Get the datastore tree')
def get_tree(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success(war_room_datastore_tree(war_room_id, _serialize))


# Folders -----------------------------------------------------------------
@war_rooms_datastore_blueprint.get('/folders')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='List datastore folders')
def list_folders(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success([_serialize_folder(f) for f in war_room_datastore_list_folders(war_room_id)])


@war_rooms_datastore_blueprint.post('/folders')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomDatastore'], summary='Create a datastore folder')
def add_folder(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    data = _json_body()
    parent_node = _as_int(data.get('parent_node'))
    folder_name = data.get('folder_name')
    if parent_node is None or not folder_name:
        return response_api_error('parent_node and folder_name are required')
    try:
        folder = war_room_datastore_add_folder(war_room_id, parent_node, folder_name)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_created(_serialize_folder(folder))


@war_rooms_datastore_blueprint.get('/folders/<int:folder_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Get a datastore folder')
def get_folder(war_room_id, folder_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        folder = war_room_datastore_get_folder(war_room_id, folder_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_success(_serialize_folder(folder))


@war_rooms_datastore_blueprint.post('/folders/<int:folder_id>/rename')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Rename a datastore folder')
def rename_folder(war_room_id, folder_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    folder_name = _json_body().get('folder_name')
    if not folder_name:
        return response_api_error('folder_name is required')
    try:
        folder = war_room_datastore_rename_folder(war_room_id, folder_id, folder_name)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_serialize_folder(folder))


@war_rooms_datastore_blueprint.post('/folders/<int:folder_id>/move')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Move a datastore folder')
def move_folder(war_room_id, folder_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    destination = _as_int(_json_body().get('destination_node'))
    if destination is None:
        return response_api_error('Missing destination_node')
    try:
        folder = war_room_datastore_move_folder(war_room_id, folder_id, destination)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_serialize_folder(folder))


@war_rooms_datastore_blueprint.delete('/folders/<int:folder_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomDatastore'], summary='Delete a datastore folder')
def delete_folder(war_room_id, folder_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        war_room_datastore_delete_folder(war_room_id, folder_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_deleted()


@war_rooms_datastore_blueprint.post('/folders/<int:folder_id>/files')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomDatastore'], summary='Upload a file to a datastore folder')
def upload_file_to_folder(war_room_id, folder_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        war_room_datastore_get_folder(war_room_id, folder_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return _upload(war_room_id, folder_id)


# Files -------------------------------------------------------------------
@war_rooms_datastore_blueprint.get('/<int:file_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Get datastore file metadata')
def get_file_meta(war_room_id, file_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        row = war_room_datastore_get(war_room_id, file_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_success(_serialize(row))


@war_rooms_datastore_blueprint.get('/<int:file_id>/content')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Download a datastore file')
def download_file(war_room_id, file_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        row = war_room_datastore_get(war_room_id, file_id)
        fh = war_room_datastore_open(row)
    except ObjectNotFoundError:
        return response_api_not_found()
    return send_file(
        fh,
        mimetype=row.mime_type or 'application/octet-stream',
        as_attachment=True,
        download_name=row.filename,
    )


@war_rooms_datastore_blueprint.post('/<int:file_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Update a datastore file')
def update_file(war_room_id, file_id):
    """Multipart: `filename`, `description`, `tags` (each left as it is
    when absent) and an optional `file` replacing the contents."""
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err

    file_storage = request.files.get('file')
    if file_storage is not None and demo_mode_over_upload_cap(file_storage):
        return response_api_error(demo_mode_upload_cap_message())

    try:
        row = war_room_datastore_update(
            war_room_id,
            file_id,
            filename=request.form.get('filename'),
            description=request.form.get('description'),
            tags=request.form.get('tags'),
            file_stream=file_storage.stream if file_storage else None,
            mime_type=file_storage.mimetype if file_storage else None,
        )
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_serialize(row))


@war_rooms_datastore_blueprint.post('/<int:file_id>/move')
@ac_api_requires()
@api_doc(tags=['WarRoomDatastore'], summary='Move a datastore file')
def move_file(war_room_id, file_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    destination = _as_int(_json_body().get('destination_node'))
    if destination is None:
        return response_api_error('Missing destination_node')
    try:
        row = war_room_datastore_move(war_room_id, file_id, destination)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_serialize(row))


@war_rooms_datastore_blueprint.delete('/<int:file_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomDatastore'], summary='Delete a datastore file')
def delete_file(war_room_id, file_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        war_room_datastore_delete(war_room_id, file_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_deleted()
