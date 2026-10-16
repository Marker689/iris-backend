#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Case datastore: business-layer re-exports of the persistence functions,
plus the module hooks a file upload, update or deletion fires.

Keeps `app.blueprints.rest.v2.case_routes.datastore` off `app.datamgmt`
directly so the layering contract passes. The hooks are what webhooks
and AI workflows subscribe to (`on_postload_datastore_file_create`, ...);
a file flagged as evidence also fires `on_postload_evidence_create` for
the evidence it creates.
"""

import datetime
import re

from app.datamgmt.datastore.datastore_db import datastore_add_child_node
from app.datamgmt.datastore.datastore_db import datastore_add_file_as_evidence as _add_file_as_evidence
from app.datamgmt.datastore.datastore_db import datastore_add_file_as_ioc
from app.datamgmt.datastore.datastore_db import datastore_delete_file as _delete_file
from app.datamgmt.datastore.datastore_db import datastore_delete_node
from app.datamgmt.datastore.datastore_db import datastore_get_file
from app.datamgmt.datastore.datastore_db import datastore_get_interactive_path_node
from app.datamgmt.datastore.datastore_db import datastore_get_local_file_path
from app.datamgmt.datastore.datastore_db import datastore_get_or_create_folder
from app.datamgmt.datastore.datastore_db import datastore_get_root
from app.datamgmt.datastore.datastore_db import datastore_get_path_node
from app.datamgmt.datastore.datastore_db import datastore_get_standard_path
from app.datamgmt.datastore.datastore_db import datastore_rename_node
from app.datamgmt.datastore.datastore_db import datastore_store_new_file
from app.datamgmt.datastore.datastore_db import ds_list_tree
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.models.errors import BusinessProcessingError
from app.models.models import DataStoreFile
from app.util import add_obj_history_entry


__all__ = [
    'datastore_add_child_node',
    'datastore_add_file_as_evidence',
    'datastore_add_file_as_ioc',
    'datastore_create_file',
    'datastore_delete_file',
    'datastore_delete_node',
    'datastore_get_file',
    'datastore_get_interactive_path_node',
    'datastore_get_local_file_path',
    'datastore_get_path_node',
    'datastore_get_standard_path',
    'datastore_hook_file_saved',
    'datastore_is_within_node',
    'datastore_rename_node',
    'ds_list_tree',
]


def datastore_add_file_as_evidence(user_identifier, dsf, caseid):
    """Adds `dsf` to the case evidences unless one with its hash is there."""
    evidence = _add_file_as_evidence(user_identifier, dsf, caseid)
    if evidence is not None:
        call_modules_hook('on_postload_evidence_create', data=evidence, caseid=caseid)
    return evidence


# Largest file a caller can write through `datastore_create_file`
DATASTORE_CREATE_FILE_MAX_BYTES = 10 * 1024 * 1024

_UNSAFE_NAME_CHARACTERS = re.compile(r'[\x00-\x1f\x7f/\\]')


def _datastore_clean_name(value, what):
    name = _UNSAFE_NAME_CHARACTERS.sub('_', str(value or '')).strip()[:255]
    if not name.strip('._'):
        raise BusinessProcessingError(f'Invalid {what}')
    return name


def datastore_create_file(case_identifier, user_identifier, filename, content: bytes, description='', tags='',
                          folder_name=None):
    """Adds `content` to the case datastore as a new file named `filename`,
    in the folder `folder_name` right under the root (created when missing)
    or in the root itself, and fires the hook of a file upload."""
    filename = _datastore_clean_name(filename, 'file name')
    if len(content) > DATASTORE_CREATE_FILE_MAX_BYTES:
        raise BusinessProcessingError(f'The file is larger than {DATASTORE_CREATE_FILE_MAX_BYTES} bytes')
    if folder_name:
        dsp = datastore_get_or_create_folder(_datastore_clean_name(folder_name, 'folder name'), case_identifier)
    else:
        dsp = datastore_get_root(case_identifier)

    dsf = DataStoreFile()
    dsf.file_original_name = filename
    dsf.file_description = description or ''
    dsf.file_tags = tags or ''
    dsf.file_parent_id = dsp.path_id
    dsf.added_by_user_id = user_identifier
    dsf.file_date_added = datetime.datetime.now()
    add_obj_history_entry(dsf, 'created')
    try:
        dsf = datastore_store_new_file(dsf, content, case_identifier)
    except OSError as e:
        raise BusinessProcessingError(f'Unable to store the file: {e}')
    datastore_hook_file_saved(dsf, case_identifier, created=True)
    return dsf


def datastore_hook_file_saved(dsf, caseid, created):
    """Fires the hook of a file uploaded (`created`) or updated in the case datastore."""
    hook_name = 'on_postload_datastore_file_create' if created else 'on_postload_datastore_file_update'
    call_modules_hook(hook_name, data=dsf, caseid=caseid)


def datastore_delete_file(file_id, caseid):
    has_error, logs = _delete_file(file_id, caseid)
    if not has_error:
        call_modules_hook('on_postload_datastore_file_delete',
                          data={'id': file_id, 'file_id': file_id, 'case_id': caseid}, caseid=caseid)
    return has_error, logs


def datastore_is_within_node(node, ancestor_id, caseid):
    """True when the folder `node` is `ancestor_id` itself or sits anywhere
    beneath it. A folder moved there would become its own ancestor: the
    loop detaches the whole branch from the root, so it vanishes from the
    tree. `seen` stops the walk on a loop already in the data."""
    seen = set()
    while node is not None and node.path_id not in seen:
        if node.path_id == ancestor_id:
            return True
        seen.add(node.path_id)
        node = datastore_get_path_node(node.path_parent_id, caseid)
    return False
