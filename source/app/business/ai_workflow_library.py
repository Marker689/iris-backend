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


"""The workflow library: the workflows shipped with IRIS and the ones
users published, each added to an instance as an inactive copy.

A user publishes one of their workflows (or a workflow document) to the
library; every user who may read workflows then sees it. What is stored
is the portable document an export gives: no owner, customer scope,
inbound token or keystore value, and a literal credential left in the
definition becomes a `key()` reference. The document must be a valid
workflow. Only the publisher or an administrator changes or removes an
entry; the shipped entries never change.
"""

from app import app
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_export
from app.business.ai_workflows import ai_workflows_import_warnings
from app.business.ai_workflows import ai_workflows_validate_definition
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_delete
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_summary
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.datamgmt.ai_workflows.ai_workflows_library_db import ai_workflows_library_db_get
from app.datamgmt.ai_workflows.ai_workflows_library_db import ai_workflows_library_db_list
from app.iris_engine.ai_workflows.library import ai_workflows_library_describe
from app.iris_engine.ai_workflows.library import ai_workflows_library_entries
from app.iris_engine.ai_workflows.library import ai_workflows_library_sort_key
from app.iris_engine.ai_workflows.portable import PortableError
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_export_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow
from app.iris_engine.utils.tracker import track_activity
from app.models.ai_workflows import AiWorkflowLibraryEntry
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_MAX_NAME = 255
_MAX_DESCRIPTION = 10_000
_MAX_CATEGORY = 64


def _iso(value):
    return value.isoformat() if value is not None else None


def _error(node_id, field, message):
    return {'node_id': node_id, 'field': field, 'message': message}


def _raise_invalid(errors):
    raise BusinessProcessingError('Invalid library entry', data={'errors': errors})


def _can_edit(entry, user_id, is_admin) -> bool:
    return bool(is_admin or (entry.owner_id is not None and entry.owner_id == user_id))


def _serialize(entry, users, user_id, is_admin) -> dict:
    return {
        'id': f'user-{entry.id}',
        'entry_id': entry.id,
        'file': None,
        **ai_workflows_library_describe(entry.document or {}, default_name=entry.name),
        'source': 'user',
        'owner_id': entry.owner_id,
        'owner': users.get(entry.owner_id),
        'can_edit': _can_edit(entry, user_id, is_admin),
        'created_at': _iso(entry.created_at),
        'updated_at': _iso(entry.updated_at),
    }


def _shipped(entry) -> dict:
    return {**entry, 'entry_id': None, 'source': 'shipped', 'owner_id': None, 'owner': None, 'can_edit': False,
            'created_at': None, 'updated_at': None}


def _with_warnings(entry, user_id) -> dict:
    workflow = entry['document'].get('workflow') or {}
    graph = workflow.get('graph') if isinstance(workflow.get('graph'), dict) else {}
    warnings = ai_workflows_import_warnings(user_id, graph.get('nodes'), workflow.get('write_tool_allowlist'))
    return {**entry, 'warnings': warnings}


def _get_editable(entry_id, user_id, is_admin) -> AiWorkflowLibraryEntry:
    entry = ai_workflows_library_db_get(entry_id)
    if entry is None:
        raise ObjectNotFoundError()
    if not _can_edit(entry, user_id, is_admin):
        raise AiWorkflowsForbiddenError('Only the user who published a library entry or an administrator can '
                                        'change it')
    return entry


def _text(value, field, limit, errors, required=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            errors.append(_error(None, field, f'{field} is required'))
        return None
    if not isinstance(value, str):
        errors.append(_error(None, field, f'{field} must be a string'))
        return None
    value = value.strip()
    if len(value) > limit:
        errors.append(_error(None, field, f'{field} is at most {limit} characters'))
    return value


def _validate(body, document) -> dict:
    """The attributes of an entry from a workflow document; `name`,
    `description` and `category` of the body replace the document's."""
    try:
        workflow = ai_workflows_portable_read_workflow(document)
    except PortableError as e:
        _raise_invalid([_error(None, 'document', str(e))])
    errors = []
    category = body['category'] if 'category' in body else document.get('category')
    name = _text(body['name'] if 'name' in body else workflow.get('name'), 'name', _MAX_NAME, errors,
                 required=True)
    description = _text(body['description'] if 'description' in body else workflow.get('description'),
                        'description', _MAX_DESCRIPTION, errors)
    category = _text(category, 'category', _MAX_CATEGORY, errors)
    workflow['name'] = name
    workflow['description'] = description
    errors.extend(ai_workflows_validate_definition(workflow)['errors'])
    if errors:
        _raise_invalid(errors)
    exported = ai_workflows_portable_export_workflow(workflow, exported_at=_iso(ai_workflows_db_utcnow()),
                                                     instance_version=app.config.get('IRIS_VERSION'))
    if category:
        exported['category'] = category
    return {'name': name, 'description': description, 'category': category, 'document': exported}


def ai_workflow_library_list(user_id, is_admin) -> list:
    """The shipped and the published workflows, by category then name,
    each with what `user_id` would lack to run it here (`warnings`, as
    on import)."""
    rows = ai_workflows_library_db_list()
    users = ai_workflows_db_user_summary([r.owner_id for r in rows if r.owner_id is not None])
    entries = [_shipped(e) for e in ai_workflows_library_entries()]
    entries.extend(_serialize(r, users, user_id, is_admin) for r in rows)
    entries.sort(key=ai_workflows_library_sort_key)
    return [_with_warnings(e, user_id) for e in entries]


def ai_workflow_library_publish(body, user_id, is_admin) -> dict:
    """Publish `{workflow_id}` (a workflow the caller owns, exported) or
    `{document}`, with an optional `name`, `description`, `category`."""
    if not isinstance(body, dict):
        _raise_invalid([_error(None, None, 'The body must be a JSON object')])
    workflow_id = body.get('workflow_id')
    if workflow_id is not None:
        if isinstance(workflow_id, bool) or not isinstance(workflow_id, int):
            _raise_invalid([_error(None, 'workflow_id', 'workflow_id must be an integer')])
        document = ai_workflows_export(workflow_id, user_id, is_admin)
    else:
        document = body.get('document')
    if not isinstance(document, dict):
        _raise_invalid([_error(None, 'document', 'A workflow_id or a workflow document is required')])
    entry = AiWorkflowLibraryEntry(**_validate(body, document), owner_id=user_id)
    ai_workflows_db_add(entry)
    ai_workflows_db_commit()
    track_activity(f'AI workflow library entry #{entry.id} "{entry.name}" published', ctx_less=True)
    users = ai_workflows_db_user_summary([user_id])
    return _with_warnings(_serialize(entry, users, user_id, is_admin), user_id)


def ai_workflow_library_update(entry_id, body, user_id, is_admin) -> dict:
    """Replace the document and/or the `name`, `description`,
    `category` of a published entry."""
    entry = _get_editable(entry_id, user_id, is_admin)
    if not isinstance(body, dict):
        _raise_invalid([_error(None, None, 'The body must be a JSON object')])
    document = body.get('document') if 'document' in body else entry.document
    if not isinstance(document, dict):
        _raise_invalid([_error(None, 'document', 'document must be a workflow document')])
    for key, value in _validate(body, document).items():
        setattr(entry, key, value)
    ai_workflows_db_commit()
    track_activity(f'AI workflow library entry #{entry.id} "{entry.name}" updated', ctx_less=True)
    users = ai_workflows_db_user_summary([entry.owner_id] if entry.owner_id is not None else [])
    return _with_warnings(_serialize(entry, users, user_id, is_admin), user_id)


def ai_workflow_library_delete(entry_id, user_id, is_admin) -> None:
    entry = _get_editable(entry_id, user_id, is_admin)
    name = entry.name
    ai_workflows_db_delete(entry)
    track_activity(f'AI workflow library entry #{entry_id} "{name}" removed', ctx_less=True)
