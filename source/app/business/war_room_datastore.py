#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""War-room datastore business layer.

Each war room gets its own subdirectory under the configured
`DATASTORE_PATH/war_rooms/<id>/`. The `WarRoomDatastoreFile` row holds
metadata (filename, size, sha256, mime, uploaded_by); the bytes live
on disk under a hashed name so two uploads with the same filename
don't clobber each other.

Files are organised in folders the way the case datastore does it: a
root `WarRoomDatastorePath` per war room, created on first use, and
subfolders under it. Folders only exist in the database, so moving a
file or a folder is a metadata change.
"""

import hashlib
import os
import uuid

from flask import current_app

from app.db import db
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomDatastoreFile
from app.models.war_rooms import WarRoomDatastorePath


_MAX_BYTES = 200 * 1024 * 1024  # 200 MiB per file — same ceiling as cases
_FILENAME_MAX = 512
_ROOT_FOLDER_NAME = 'root'


def _datastore_root():
    return current_app.config.get(
        'DATASTORE_PATH', '/home/iris/server_data/datastore'
    )


def _war_room_dir(war_room_id):
    root = _datastore_root()
    return os.path.join(root, 'war_rooms', str(war_room_id))


def _sanitize_filename(name):
    if not isinstance(name, str) or not name.strip():
        raise BusinessProcessingError('filename is required')
    base = os.path.basename(name).strip()
    if not base or base in ('.', '..'):
        raise BusinessProcessingError('Invalid filename')
    return base[:_FILENAME_MAX]


def _sanitize_folder_name(name):
    if not isinstance(name, str) or not name.strip():
        raise BusinessProcessingError('folder_name is required')
    return name.strip()[:_FILENAME_MAX]


def _remove_blob(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            # Don't roll back the DB change — leaving an orphan blob is
            # less bad than leaving the row pointing at it.
            pass


def _write_blob(war_room_id, file_stream, filename):
    """Stream an upload to disk in 64 KiB chunks while updating a sha256
    hash, so the whole upload is never held in memory. Returns
    `(storage_path, size, sha256)`."""
    dirpath = _war_room_dir(war_room_id)
    os.makedirs(dirpath, exist_ok=True)

    # Random suffix prevents collision while keeping the original
    # filename in the DB row for the UI.
    storage_path = os.path.join(dirpath, f'{uuid.uuid4().hex}_{filename}')

    hasher = hashlib.sha256()
    total = 0
    with open(storage_path, 'wb') as out:
        while True:
            chunk = file_stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_BYTES:
                out.close()
                _remove_blob(storage_path)
                raise BusinessProcessingError(
                    f'File exceeds {_MAX_BYTES // (1024 * 1024)} MiB cap'
                )
            hasher.update(chunk)
            out.write(chunk)

    if total == 0:
        _remove_blob(storage_path)
        raise BusinessProcessingError('Empty file')

    return storage_path, total, hasher.hexdigest()


def war_room_datastore_list(war_room_id, include_attached_cases=True):
    """Return the war-room's own files.

    `include_attached_cases` is currently informational — the SPA
    fetches per-case datastore listings via the existing case
    endpoints. We don't aggregate server-side here because that would
    require teaching this layer about case-level access nuances; the
    SPA already has the per-case ACL coverage.
    """
    rows = (
        WarRoomDatastoreFile.query
        .filter(WarRoomDatastoreFile.war_room_id == war_room_id)
        .order_by(WarRoomDatastoreFile.uploaded_at.desc())
        .all()
    )
    return rows


def war_room_datastore_get(war_room_id, file_id):
    row = WarRoomDatastoreFile.query.filter_by(
        war_room_id=war_room_id, file_id=file_id
    ).first()
    if row is None:
        raise ObjectNotFoundError()
    return row


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------
def war_room_datastore_get_root(war_room_id):
    """The root folder of the war room, created on first use."""
    root = WarRoomDatastorePath.query.filter_by(
        war_room_id=war_room_id, path_is_root=True
    ).order_by(WarRoomDatastorePath.path_id).first()
    if root is not None:
        return root

    root = WarRoomDatastorePath()
    root.war_room_id = war_room_id
    root.path_name = _ROOT_FOLDER_NAME
    root.path_parent_id = None
    root.path_is_root = True
    db.session.add(root)
    db.session.commit()
    return root


def war_room_datastore_list_folders(war_room_id):
    war_room_datastore_get_root(war_room_id)
    return (
        WarRoomDatastorePath.query
        .filter(WarRoomDatastorePath.war_room_id == war_room_id)
        .order_by(WarRoomDatastorePath.path_id)
        .all()
    )


def war_room_datastore_get_folder(war_room_id, folder_id):
    folder = WarRoomDatastorePath.query.filter_by(
        war_room_id=war_room_id, path_id=folder_id
    ).first()
    if folder is None:
        raise ObjectNotFoundError()
    return folder


def _folder_ids_under(folders, folder_id):
    """`folder_id` and the ids of every folder beneath it, out of
    `folders` (all the folders of one war room). `seen` stops the walk on
    a loop already in the data."""
    children = {}
    for folder in folders:
        children.setdefault(folder.path_parent_id, []).append(folder.path_id)
    seen = set()
    pending = [folder_id]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(children.get(current, []))
    return seen


def war_room_datastore_add_folder(war_room_id, parent_id, folder_name):
    try:
        parent = war_room_datastore_get_folder(war_room_id, parent_id)
    except ObjectNotFoundError:
        raise BusinessProcessingError('Parent node is invalid for this war room')

    folder = WarRoomDatastorePath()
    folder.war_room_id = war_room_id
    folder.path_name = _sanitize_folder_name(folder_name)
    folder.path_parent_id = parent.path_id
    folder.path_is_root = False
    db.session.add(folder)
    db.session.commit()
    track_activity(f'added folder "{folder.path_name}" to datastore',
                   war_room_id=war_room_id)
    return folder


def war_room_datastore_rename_folder(war_room_id, folder_id, folder_name):
    folder = war_room_datastore_get_folder(war_room_id, folder_id)
    previous_name = folder.path_name
    folder.path_name = _sanitize_folder_name(folder_name)
    db.session.commit()
    track_activity(f'renamed datastore folder "{previous_name}" to "{folder.path_name}"',
                   war_room_id=war_room_id)
    return folder


def war_room_datastore_move_folder(war_room_id, folder_id, destination_id):
    folder = war_room_datastore_get_folder(war_room_id, folder_id)
    try:
        destination = war_room_datastore_get_folder(war_room_id, destination_id)
    except ObjectNotFoundError:
        raise BusinessProcessingError('Invalid destination node ID for this war room')

    if folder.path_is_root:
        raise BusinessProcessingError('The root folder cannot be moved')
    if folder.path_id == destination.path_id:
        raise BusinessProcessingError('Source and destination folders are the same')

    folders = WarRoomDatastorePath.query.filter_by(war_room_id=war_room_id).all()
    if destination.path_id in _folder_ids_under(folders, folder.path_id):
        raise BusinessProcessingError('A folder cannot be moved into one of its own subfolders')

    folder.path_parent_id = destination.path_id
    db.session.commit()
    track_activity(f'moved datastore folder "{folder.path_name}" to "{destination.path_name}"',
                   war_room_id=war_room_id)
    return folder


def war_room_datastore_delete_folder(war_room_id, folder_id):
    """Delete a folder, its subfolders and every file in them, bytes
    included. Each file fires the same hook as a single file deletion."""
    folder = war_room_datastore_get_folder(war_room_id, folder_id)
    if folder.path_is_root:
        raise BusinessProcessingError('Cannot delete root folder')

    folders = WarRoomDatastorePath.query.filter_by(war_room_id=war_room_id).all()
    folder_ids = _folder_ids_under(folders, folder.path_id)
    files = WarRoomDatastoreFile.query.filter(
        WarRoomDatastoreFile.war_room_id == war_room_id,
        WarRoomDatastoreFile.file_parent_id.in_(folder_ids)
    ).all()

    deleted = [(row.file_id, row.storage_path) for row in files]
    for row in files:
        db.session.delete(row)
    WarRoomDatastorePath.query.filter(
        WarRoomDatastorePath.war_room_id == war_room_id,
        WarRoomDatastorePath.path_id.in_(folder_ids)
    ).delete(synchronize_session=False)
    db.session.commit()

    for file_id, path in deleted:
        _remove_blob(path)
        call_modules_hook('on_postload_war_room_datastore_file_delete',
                          {'war_room_id': war_room_id, 'file_id': file_id, 'id': file_id})
    track_activity(f'deleted datastore folder "{folder.path_name}" and its {len(deleted)} file(s)',
                   war_room_id=war_room_id)


def war_room_datastore_tree(war_room_id, serialize_file):
    """The datastore as the nested dict the case datastore tree endpoint
    returns: folders under `d-<id>` keys with a `children` dict, files
    under `f-<id>` keys. `serialize_file` turns a file row into its node."""
    root = war_room_datastore_get_root(war_room_id)
    folders = WarRoomDatastorePath.query.filter(
        WarRoomDatastorePath.war_room_id == war_room_id,
        WarRoomDatastorePath.path_id != root.path_id
    ).all()
    files = WarRoomDatastoreFile.query.filter(
        WarRoomDatastoreFile.war_room_id == war_room_id
    ).all()
    return _build_tree(root, folders, files, serialize_file)


def _build_tree(root, folders, files, serialize_file):
    """Nest `folders` and `files` under `root`. A folder or file whose
    parent is missing lands in the root rather than vanishing."""
    nodes = {root.path_id: {
        'name': root.path_name,
        'type': 'directory',
        'is_root': True,
        'children': {},
    }}
    for folder in folders:
        nodes[folder.path_id] = {
            'name': folder.path_name,
            'type': 'directory',
            'is_root': False,
            'children': {},
        }

    for folder in sorted(folders, key=lambda f: (f.path_name or '').lower()):
        parent_id = folder.path_parent_id
        if parent_id not in nodes or parent_id == folder.path_id:
            parent_id = root.path_id
        nodes[parent_id]['children'][f'd-{folder.path_id}'] = nodes[folder.path_id]

    for row in sorted(files, key=lambda f: (f.filename or '').lower()):
        parent_id = row.file_parent_id if row.file_parent_id in nodes else root.path_id
        node = serialize_file(row)
        node['type'] = 'file'
        nodes[parent_id]['children'][f'f-{row.file_id}'] = node

    return {f'd-{root.path_id}': nodes[root.path_id]}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def war_room_datastore_save(war_room_id, file_stream, filename,
                             mime_type=None, description=None, tags=None,
                             uploaded_by_id=None, folder_id=None):
    """Persist an uploaded file in the folder `folder_id`, the root when
    it is None. Caller is expected to have already validated war-room
    write access.
    """
    filename = _sanitize_filename(filename)
    if folder_id is None:
        folder = war_room_datastore_get_root(war_room_id)
    else:
        try:
            folder = war_room_datastore_get_folder(war_room_id, folder_id)
        except ObjectNotFoundError:
            raise BusinessProcessingError('Invalid folder for this war room')

    storage_path, total, sha256 = _write_blob(war_room_id, file_stream, filename)

    row = WarRoomDatastoreFile()
    row.war_room_id = war_room_id
    row.filename = filename
    row.description = description
    row.storage_path = storage_path
    row.size_bytes = total
    row.mime_type = mime_type
    row.sha256 = sha256
    row.uploaded_by_id = uploaded_by_id
    row.tags = tags
    row.file_parent_id = folder.path_id
    db.session.add(row)
    db.session.commit()
    track_activity(f'uploaded file "{row.filename}" ({total} bytes) to datastore',
                   war_room_id=war_room_id)
    row = call_modules_hook('on_postload_war_room_datastore_file_create', row)
    return row


def war_room_datastore_update(war_room_id, file_id, filename=None, description=None,
                              tags=None, file_stream=None, mime_type=None):
    """Update the metadata of a file — `None` leaves a field as it is —
    and, when `file_stream` is given, replace its bytes."""
    row = war_room_datastore_get(war_room_id, file_id)
    if filename is not None:
        row.filename = _sanitize_filename(filename)
    if description is not None:
        row.description = description
    if tags is not None:
        row.tags = tags

    previous_path = None
    if file_stream is not None:
        storage_path, total, sha256 = _write_blob(war_room_id, file_stream, row.filename)
        previous_path = row.storage_path
        row.storage_path = storage_path
        row.size_bytes = total
        row.sha256 = sha256
        row.mime_type = mime_type

    db.session.commit()
    if previous_path:
        _remove_blob(previous_path)
    track_activity(f'updated file "{row.filename}" in datastore',
                   war_room_id=war_room_id)
    return row


def war_room_datastore_move(war_room_id, file_id, destination_id):
    row = war_room_datastore_get(war_room_id, file_id)
    try:
        destination = war_room_datastore_get_folder(war_room_id, destination_id)
    except ObjectNotFoundError:
        raise BusinessProcessingError('Invalid destination node ID for this war room')

    row.file_parent_id = destination.path_id
    db.session.commit()
    track_activity(f'moved file "{row.filename}" to "{destination.path_name}" in datastore',
                   war_room_id=war_room_id)
    return row


def war_room_datastore_open(row):
    """Return a binary file handle to the stored bytes."""
    if not row.storage_path or not os.path.exists(row.storage_path):
        raise ObjectNotFoundError()
    return open(row.storage_path, 'rb')


def war_room_datastore_delete(war_room_id, file_id):
    row = war_room_datastore_get(war_room_id, file_id)
    path = row.storage_path
    filename = row.filename
    db.session.delete(row)
    db.session.commit()
    _remove_blob(path)
    track_activity(f'deleted file "{filename}" from datastore',
                   war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_datastore_file_delete',
                      {'war_room_id': war_room_id, 'file_id': file_id, 'id': file_id})


def war_room_attached_cases(war_room_id):
    """Convenience used by the SPA to fan out per-case datastore reads."""
    rows = (
        WarRoomCase.query
        .with_entities(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .all()
    )
    return [r.case_id for r in rows]
