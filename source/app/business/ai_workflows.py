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

"""AI workflows: definition CRUD and versioning, manual runs, run ledger
listing / detail / export, inbound token rotation and the editor
catalogue.

Callers pass the acting user id and whether that user is a server
administrator; the REST layer derives both from the request (so an API
key scope mask is honoured). Visibility rules:

- workflows: administrators see every workflow, everyone else the ones
  they own and the ones shared with one of their groups. A member of a
  group it is shared with may read it, its versions and its node
  events, and run it manually; one of a group it is shared with for
  editing may also edit, test and replay it, but not change the limits
  its owner set (activation, allowlist, customer scope, budgets,
  audience) nor add a keystore entry they cannot use. Only the owner
  (or an administrator) sets those, shares, files, exports, publishes or
  deletes it;
- runs: administrators see every run; everyone else must have owned the
  workflow when the run started (`run.owner_id`), be the run-as user or
  the user who triggered the run, AND be able to access the run's
  entity when it has one;
- full LLM snapshots and the JSON export: administrators and the
  workflow owner only;
- what an analyst did on a run (the result of a suggestion they
  accepted, the answer they gave): that analyst only — everyone else
  sees who resolved it, not the data.

An object the caller may not see is reported as not found (404), so
nothing can be probed; a shared workflow the caller may not change is
forbidden (403). Runs started by the owner or an administrator act as
the workflow owner; a manual run (or re-run), test or replay started
by a member of a group the workflow is shared with (editor or not)
acts as that member, like a run from
an object menu: their permissions, entity access and personal keys,
never the owner's. `scope_mask` is the API-key scope of the request
(None for a session or an unscoped key) and bounds the run.
"""

import copy
import datetime
import hashlib
import json
import uuid

from app import app
from app.business.access_controls import access_controls_user_accessible_customers
from app.business.categories import categories_resolve_id
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_involved_runs
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_node_stats
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_node_step_candidates
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_runs_by_ids
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_steps_by_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_active_runs_for_workflow
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_delete
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_customer
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_exists
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_title
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_run
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_run_by_uuid
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_step
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_version
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_existing_group_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_group_names
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_inbound_events
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_run_uuids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_inbound_events_for_run
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_inbound_events_for_workflows
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_llm_calls
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_runs
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_steps
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_suggestions
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_tool_calls
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_versions
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_waits
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_lock_workflow
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_owned_workflow_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_postload_hooks
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_run_counts
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_group_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_user_summary
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_access_denial
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_cancel_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_hash_token
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_inbound_url
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_insert_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_new_token
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_replay_context
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_replay_step
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_test_context
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_test_node
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_scope_allows
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_user_can_access
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_nodes
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_examples
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_render
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_trigger_ids
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_upgrade_triggers
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_node
from app.iris_engine.ai_workflows.keystore import ai_workflows_keystore_visible_entries
from app.iris_engine.ai_workflows.live import ai_workflows_live_editor_room
from app.iris_engine.ai_workflows.live import ai_workflows_live_emit_saved
from app.iris_engine.ai_workflows.live import ai_workflows_live_run_room
from app.iris_engine.ai_workflows.live import ai_workflows_live_workflow_room
from app.iris_engine.ai_workflows.nodes import NODE_NOTE
from app.iris_engine.ai_workflows.nodes import NODE_RUN_WORKFLOW
from app.iris_engine.ai_workflows.nodes import NODE_TRIGGER
from app.iris_engine.ai_workflows.nodes import ai_workflows_nodes_catalogue
from app.iris_engine.ai_workflows.nodes import ai_workflows_nodes_is_waiting
from app.iris_engine.ai_workflows.portable import PortableError
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_export_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_key_references
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_literal_secrets
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_tools
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_check
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_serialize
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_user_can_see
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_catalogue
from app.iris_engine.llm.client import load_config
from app.iris_engine.mail.secrets import encrypt_secret
from app.iris_engine.utils.tracker import track_activity
from app.models.ai_workflows import AUDIENCES
from app.models.ai_workflows import AUDIENCE_ENTITY
from app.models.ai_workflows import AiWorkflow
from app.models.ai_workflows import AiWorkflowVersion
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import EXEC_ACCEPTED_BY_USER
from app.models.ai_workflows import RUN_ACTIVE_STATUSES
from app.models.ai_workflows import RUN_STATUSES
from app.models.ai_workflows import STEP_FAILED
from app.models.ai_workflows import STEP_RESUMED
from app.models.ai_workflows import STEP_SUCCEEDED
from app.models.ai_workflows import STEP_WAITING
from app.models.ai_workflows import SUGGESTION_KINDS
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.ai_workflows import TRIGGER_TYPES
from app.models.ai_workflows import TRIGGER_WEBHOOK
from app.models.ai_workflows import ai_workflow_trigger_config
from app.models.ai_workflows import ai_workflow_trigger_of_node
from app.models.ai_workflows import ai_workflow_triggers
from app.models.authorization import Permissions
from app.models.categories import CATEGORY_KIND_AI_WORKFLOW
from app.models.authorization import ac_has_permission_server_administrator
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


class AiWorkflowsForbiddenError(BusinessProcessingError):
    """The caller is authenticated and holds the permission bit, but may
    not act on this object (REST: 403)."""

    def __init__(self, message='Permission denied'):
        super().__init__(message)


class AiWorkflowsDisabledError(AiWorkflowsForbiddenError):
    """`AI_WORKFLOWS_ENABLED` is off (REST: 403)."""

    def __init__(self, message='AI workflows are disabled on this server'):
        super().__init__(message)


class AiWorkflowsConflictError(BusinessProcessingError):
    """The workflow changed since the caller loaded it (REST: 409); the
    data carries the current revision and version."""

    def __init__(self, revision, version):
        super().__init__('The workflow was changed by someone else since it was loaded',
                         data={'revision': revision, 'version': version})


# Fields whose change produces a new version: everything that alters
# what a run does or whom it acts as
_VERSIONED_FIELDS = ('triggers', 'trigger_type', 'trigger_config', 'customer_scope', 'graph', 'owner_id',
                     'write_tool_allowlist', 'max_runs_per_hour', 'token_budget_per_run',
                     'suggestion_audience')
_DEFINITION_FIELDS = ('name', 'description', 'is_active') + _VERSIONED_FIELDS
# What an editor who does not manage the workflow cannot change
_OWNER_FIELDS = ('is_active', 'owner_id', 'customer_scope', 'write_tool_allowlist', 'max_runs_per_hour',
                 'token_budget_per_run', 'suggestion_audience')

_MAX_RUNS_PER_HOUR = 10000
_MAX_TOKEN_BUDGET = 10_000_000
_MAX_PER_PAGE = 100
_MAX_DESCRIPTION = 10_000
_MAX_ALLOWLIST = 200
_MAX_CUSTOMER_SCOPE = 1000
_MAX_SHARED_GROUPS = 200
# Run detail / export: a tool-call result over this is cut, and a step
# shows at most this many tool calls
_MAX_TOOL_RESULT_CHARS = 16 * 1024
_MAX_TOOL_CALLS_PER_STEP = 200
# Non-administrator run listing: newest runs examined for visibility
_MAX_RUN_SCAN = 5000
# JSON size of the upstream outputs / variables / payload a node test supplies
_MAX_TEST_CONTEXT_BYTES = 256 * 1024


# ---- Helpers ---------------------------------------------------------------

def _iso(value):
    return value.isoformat() if value is not None else None


def _str_uuid(value):
    return str(value) if value is not None else None


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _error(node_id, field, message):
    return {'node_id': node_id, 'field': field, 'message': message}


def _dedup_errors(errors) -> list:
    seen = set()
    result = []
    for e in errors:
        key = (e.get('node_id'), e.get('field'), e.get('message'))
        if key in seen:
            continue
        seen.add(key)
        result.append(e)
    return result


def _raise_invalid(errors):
    raise BusinessProcessingError('Invalid workflow', data={'errors': _dedup_errors(errors)})


def _as_int(value, field, errors, minimum, maximum, default):
    if value is None:
        return default
    if isinstance(value, bool):
        errors.append(_error(None, field, f'{field} must be an integer'))
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors.append(_error(None, field, f'{field} must be an integer'))
        return default
    if number < minimum or number > maximum:
        errors.append(_error(None, field, f'{field} must be between {minimum} and {maximum}'))
    return number


def _user_summary(user_id, users=None):
    if user_id is None:
        return None
    if users is not None and user_id in users:
        return users[user_id]
    return ai_workflows_db_user_summary([user_id]).get(user_id)


# ---- Serialization ---------------------------------------------------------

def _definition(workflow) -> dict:
    """The behaviour-defining snapshot a version row keeps; a graph saved
    before trigger nodes carried their trigger is shown upgraded."""
    graph = ai_workflows_graph_upgrade_triggers(workflow.graph or {}, ai_workflow_triggers(workflow))
    return {
        'name': workflow.name,
        'description': workflow.description,
        'is_active': bool(workflow.is_active),
        'triggers': _graph_triggers(graph) or ai_workflow_triggers(workflow),
        'trigger_type': workflow.trigger_type,
        'trigger_config': workflow.trigger_config or {},
        'customer_scope': workflow.customer_scope or [],
        'graph': graph,
        'owner_id': workflow.owner_id,
        'write_tool_allowlist': workflow.write_tool_allowlist or [],
        'max_runs_per_hour': workflow.max_runs_per_hour,
        'token_budget_per_run': workflow.token_budget_per_run,
        'suggestion_audience': workflow.suggestion_audience,
    }


def _revision(definition) -> str:
    """Fingerprint of a definition, the concurrency token of a save: it
    moves with every change of it, name and switch included, and with
    nothing else (unlike `updated_at`, which a trigger firing moves)."""
    encoded = json.dumps(definition, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def ai_workflows_serialize(workflow, counts=None, users=None, with_graph=True, groups=None,
                           can_edit=True, can_manage=True) -> dict:
    """`groups` maps group ids to names (looked up when None). `can_edit`
    tells the caller whether they may edit the workflow, or only see and
    run it (shared with one of their groups); `can_manage` whether they
    may also set its limits, share, export or delete it (owner, admin)."""
    data = _definition(workflow)
    revision = _revision(data)
    if not with_graph:
        data.pop('graph', None)
    is_webhook = ai_workflow_trigger_config(workflow, TRIGGER_WEBHOOK) is not None
    shared_group_ids = list(getattr(workflow, 'shared_group_ids', None) or [])
    if groups is None:
        groups = ai_workflows_db_group_names(shared_group_ids) if shared_group_ids else {}
    data.update({
        'id': workflow.id,
        'uuid': _str_uuid(workflow.uuid),
        'owner': _user_summary(workflow.owner_id, users),
        'version': workflow.version,
        'revision': revision,
        'has_inbound_token': bool(workflow.inbound_token_hash),
        'has_signing_secret': bool(getattr(workflow, 'inbound_signing_secret', None)),
        'skipped_count': getattr(workflow, 'skipped_count', None) or 0,
        'last_skip_reason': getattr(workflow, 'last_skip_reason', None),
        'last_skipped_at': _iso(getattr(workflow, 'last_skipped_at', None)),
        'inbound_url': ai_workflows_engine_inbound_url(str(workflow.uuid)) if is_webhook and workflow.uuid else None,
        'last_fired_at': _iso(workflow.last_fired_at),
        'category_id': getattr(workflow, 'category_id', None),
        'shared_group_ids': shared_group_ids,
        'shared_groups': [{'id': i, 'name': groups.get(i)} for i in shared_group_ids if i in groups],
        'shared_edit_group_ids': list(getattr(workflow, 'shared_edit_group_ids', None) or []),
        'can_edit': bool(can_edit),
        'can_manage': bool(can_manage),
        'created_by_id': workflow.created_by_id,
        'created_at': _iso(workflow.created_at),
        'updated_at': _iso(workflow.updated_at),
    })
    if counts is not None:
        data['run_counts_24h'] = counts
    return data


def ai_workflows_run_summary(run, users=None, titles=None) -> dict:
    users = users if users is not None else ai_workflows_db_user_summary([run.run_as_user_id,
                                                                         run.triggered_by_user_id])
    title_key = (run.entity_type, run.entity_id)
    if titles is not None and title_key in titles:
        entity_title = titles[title_key]
    else:
        entity_title = ai_workflows_db_entity_title(run.entity_type, run.entity_id) if run.entity_id else None
        if titles is not None:
            titles[title_key] = entity_title
    return {
        'id': run.id,
        'uuid': _str_uuid(run.uuid),
        'workflow_id': run.workflow_id,
        'workflow_name': run.workflow_name,
        'workflow_version': run.workflow_version,
        'status': run.status,
        'trigger_type': run.trigger_type,
        'entity_type': run.entity_type,
        'entity_id': run.entity_id,
        'entity_title': entity_title,
        'sub_entity': run.sub_entity,
        'run_as': users.get(run.run_as_user_id),
        'triggered_by': users.get(run.triggered_by_user_id),
        'is_dry_run': bool(run.is_dry_run),
        'tokens_used': run.tokens_used or 0,
        'step_count': run.step_count or 0,
        'error': run.error,
        'waiting_node_id': run.waiting_node_id,
        'started_at': _iso(run.started_at),
        'updated_at': _iso(run.updated_at),
        'finished_at': _iso(run.finished_at),
        'chain_depth': run.chain_depth or 0,
        'parent_run_id': run.parent_run_id,
        'replayed_from_step_id': getattr(run, 'replayed_from_step_id', None),
        'tested_node_id': getattr(run, 'tested_node_id', None),
    }


def _step_public(step) -> dict:
    return {
        'id': step.id,
        'seq': step.seq,
        'node_id': step.node_id,
        'node_type': step.node_type,
        'node_label': step.node_label,
        'status': step.status,
        'input': step.input,
        'output': step.output,
        'port': step.port,
        'error': step.error,
        'tokens_used': step.tokens_used or 0,
        'started_at': _iso(step.started_at),
        'ended_at': _iso(step.ended_at),
    }


def _hidden_from(resolver_id, viewer_id) -> bool:
    """What an analyst did (accepted result, answer) is theirs alone."""
    return resolver_id is not None and resolver_id != viewer_id


def _cap_result(result):
    """(result, truncated): a result whose JSON is over the cap becomes
    the first `_MAX_TOOL_RESULT_CHARS` characters of that JSON."""
    if result is None:
        return None, False
    try:
        text = json.dumps(result, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) <= _MAX_TOOL_RESULT_CHARS:
        return result, False
    return text[:_MAX_TOOL_RESULT_CHARS], True


def _tool_call_public(call, users, viewer_id=None) -> dict:
    hidden = call.execution_mode == EXEC_ACCEPTED_BY_USER and _hidden_from(call.acting_user_id, viewer_id)
    result, truncated = (None, False) if hidden else _cap_result(call.result)
    return {
        'id': call.id,
        'step_id': call.step_id,
        'suggestion_id': call.suggestion_id,
        'tool_name': call.tool_name,
        'arguments': call.arguments,
        'result': result,
        'result_truncated': truncated,
        'result_hidden': hidden,
        'error': call.error,
        'classification': call.classification,
        'execution_mode': call.execution_mode,
        'acting_user_id': call.acting_user_id,
        'acting_user': users.get(call.acting_user_id),
        'duration_ms': call.duration_ms,
        'created_at': _iso(call.created_at),
    }


def _cap_tool_calls(tool_calls) -> tuple:
    """(kept, omitted count): at most `_MAX_TOOL_CALLS_PER_STEP` per step."""
    per_step = {}
    kept = []
    for call in tool_calls:
        count = per_step.get(call.step_id, 0)
        per_step[call.step_id] = count + 1
        if count < _MAX_TOOL_CALLS_PER_STEP:
            kept.append(call)
    return kept, len(tool_calls) - len(kept)


def _llm_call_public(call, with_snapshots) -> dict:
    return {
        'id': call.id,
        'step_id': call.step_id,
        'user_id': call.user_id,
        'provider': call.provider,
        'model': call.model,
        'policy_id': call.policy_id,
        'restriction_level': call.restriction_level,
        'redacted': bool(call.redacted),
        'prompt_tokens': call.prompt_tokens or 0,
        'completion_tokens': call.completion_tokens or 0,
        'bytes_sent': call.bytes_sent or 0,
        'error': call.error,
        'created_at': _iso(call.created_at),
        'snapshots_visible': bool(with_snapshots),
        'request_snapshot': call.request_snapshot if with_snapshots else None,
        'response_snapshot': call.response_snapshot if with_snapshots else None,
    }


def _wait_public(wait, users, viewer_id=None) -> dict:
    hidden = _hidden_from(wait.resolved_by_id, viewer_id)
    return {
        'id': wait.id,
        'uuid': _str_uuid(wait.uuid),
        'node_id': wait.node_id,
        'kind': wait.kind,
        'status': wait.status,
        'expires_at': _iso(wait.expires_at),
        'resolved_payload': None if hidden else wait.resolved_payload,
        'resolved_payload_hidden': hidden,
        'resolved_by_id': wait.resolved_by_id,
        'resolved_by': users.get(wait.resolved_by_id),
        'resolved_at': _iso(wait.resolved_at),
        'source_ip': wait.source_ip,
        'payload_sha256': wait.payload_sha256,
        'suggestion_id': wait.suggestion_id,
        'created_at': _iso(wait.created_at),
    }


def ai_workflows_suggestion_public(suggestion, viewer_id) -> dict:
    """A suggestion as `viewer_id` may see it: the result of an accepted
    action and the answer to a question only go to whoever resolved it;
    others see `resolved_by` and `resolution_hidden: true`."""
    data = ai_workflows_suggestions_serialize(suggestion)
    hidden = _hidden_from(suggestion.resolved_by_id, viewer_id)
    if hidden:
        data['result'] = None
        data['answer'] = None
    data['resolution_hidden'] = hidden
    return data


def ai_workflows_inbound_event_public(event, run_uuids=None) -> dict:
    return {
        'id': event.id,
        'kind': event.kind,
        'workflow_id': event.workflow_id,
        'wait_id': event.wait_id,
        'run_id': event.run_id,
        'run_uuid': (run_uuids or {}).get(event.run_id),
        'source_ip': event.source_ip,
        'status': event.status,
        'reason': event.reason,
        'payload_sha256': event.payload_sha256,
        'payload_bytes': event.payload_bytes or 0,
        'created_at': _iso(event.created_at),
    }


# ---- Access ----------------------------------------------------------------

def _get_workflow(workflow_id) -> AiWorkflow:
    workflow = ai_workflows_db_get(workflow_id)
    if workflow is None:
        raise ObjectNotFoundError()
    return workflow


def _shared_with(group_ids, user_id, user_group_ids=None) -> bool:
    """Whether the user is in one of `group_ids` (`user_group_ids`: theirs,
    looked up only when needed and not given)."""
    if not group_ids:
        return False
    if user_group_ids is None:
        user_group_ids = ai_workflows_db_user_group_ids(user_id)
    return bool(set(group_ids) & set(user_group_ids))


def _is_shared_with(workflow, user_id, user_group_ids=None) -> bool:
    return _shared_with(workflow.shared_group_ids, user_id, user_group_ids)


def _can_manage(workflow, user_id, is_admin) -> bool:
    """The owner or an administrator: they alone set the limits of the
    workflow, share, file, export, publish or delete it."""
    return is_admin or workflow.owner_id == user_id


def _can_edit(workflow, user_id, is_admin, user_group_ids=None) -> bool:
    """The owner, an administrator, or a member of a group the workflow is
    shared with for editing."""
    return _can_manage(workflow, user_id, is_admin) or _shared_with(
        getattr(workflow, 'shared_edit_group_ids', None), user_id, user_group_ids)


def _get_visible_workflow(workflow_id, user_id, is_admin) -> AiWorkflow:
    """A workflow someone else owns and did not share with a group of the
    user does not exist for a non-admin."""
    workflow = _get_workflow(workflow_id)
    if not _can_manage(workflow, user_id, is_admin) and not _is_shared_with(workflow, user_id):
        raise ObjectNotFoundError()
    return workflow


def _get_editable_workflow(workflow_id, user_id, is_admin) -> AiWorkflow:
    """The owner, an administrator or an editor (shared for editing) may
    edit a workflow: one only shared with the user is forbidden."""
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    if not _can_edit(workflow, user_id, is_admin):
        raise AiWorkflowsForbiddenError('This workflow is shared with you, not for editing')
    return workflow


def _get_owned_workflow(workflow_id, user_id, is_admin) -> AiWorkflow:
    """Only the owner (or an administrator) may manage a workflow: one
    shared with the user is forbidden, any other does not exist."""
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    if not _can_manage(workflow, user_id, is_admin):
        raise AiWorkflowsForbiddenError('Only the owner of this workflow can do that')
    return workflow


def _run_as(workflow, user_id, is_admin):
    """Who a run started by `user_id` acts as: the owner when the user
    manages the workflow, else the user (an editor or a member)."""
    return workflow.owner_id if _can_manage(workflow, user_id, is_admin) else user_id


def _run_owner_id(run):
    """Owner of the workflow when the run started; the current owner
    (or the snapshot) for runs that predate `owner_id`."""
    owner_id = getattr(run, 'owner_id', None)
    if owner_id is not None:
        return owner_id
    if run.workflow is not None:
        return run.workflow.owner_id
    snapshot = run.definition_snapshot or {}
    return snapshot.get('owner_id')


def _entity_visible(user_id, entity_type, entity_id, access_cache=None, scope_mask=None) -> bool:
    if not entity_type or entity_id is None:
        return True
    if not ai_workflows_entities_scope_allows(entity_type, scope_mask):
        return False
    key = (entity_type, entity_id)
    if access_cache is not None and key in access_cache:
        return access_cache[key]
    allowed = bool(ai_workflows_entities_user_can_access(user_id, entity_type, entity_id))
    if access_cache is not None:
        access_cache[key] = allowed
    return allowed


def ai_workflows_user_can_see_run(user_id, is_admin, run, access_cache=None, scope_mask=None) -> bool:
    if is_admin:
        return True
    involved = user_id in (run.run_as_user_id, run.triggered_by_user_id, _run_owner_id(run))
    if not involved:
        return False
    return _entity_visible(user_id, run.entity_type, run.entity_id, access_cache, scope_mask)


def _run_privileged(user_id, is_admin, run) -> bool:
    """Admin or workflow owner: full LLM snapshots and export."""
    return is_admin or _run_owner_id(run) == user_id


def _get_visible_run(run_uuid, user_id, is_admin, scope_mask=None):
    parsed = _parse_uuid(run_uuid)
    if parsed is None:
        raise ObjectNotFoundError()
    run = ai_workflows_db_get_run_by_uuid(parsed)
    if run is None or not ai_workflows_user_can_see_run(user_id, is_admin, run, scope_mask=scope_mask):
        raise ObjectNotFoundError()
    return run


# ---- Validation ------------------------------------------------------------

def _owner_accessible_customers(owner):
    """None = every customer (administrator)."""
    permissions = ac_get_effective_permissions_of_user(owner)
    return access_controls_user_accessible_customers(owner, permissions)


def _validate_customer_scope(scope, owner, errors):
    if scope is None:
        return []
    if not isinstance(scope, list):
        errors.append(_error(None, 'customer_scope', 'customer_scope must be a list of customer ids'))
        return []
    if len(scope) > _MAX_CUSTOMER_SCOPE:
        errors.append(_error(None, 'customer_scope', f'customer_scope is limited to {_MAX_CUSTOMER_SCOPE} customers'))
        return []
    ids = []
    for value in scope:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            errors.append(_error(None, 'customer_scope', 'customer_scope must be a list of customer ids'))
            return []
        try:
            ids.append(int(value))
        except ValueError:
            errors.append(_error(None, 'customer_scope', 'customer_scope must be a list of customer ids'))
            return []
    ids = sorted(set(ids))
    if owner is None or not ids:
        return ids
    accessible = _owner_accessible_customers(owner)
    if accessible is not None:
        outside = [i for i in ids if i not in accessible]
        if outside:
            errors.append(_error(None, 'customer_scope',
                                 f'The workflow owner has no access to customer(s) {outside}'))
    return ids


def _validate_allowlist(allowlist, errors, tools=None):
    if allowlist is None:
        return []
    if not isinstance(allowlist, list) or not all(isinstance(t, str) for t in allowlist):
        errors.append(_error(None, 'write_tool_allowlist', 'write_tool_allowlist must be a list of tool names'))
        return []
    if len(set(allowlist)) > _MAX_ALLOWLIST:
        errors.append(_error(None, 'write_tool_allowlist',
                             f'write_tool_allowlist holds at most {_MAX_ALLOWLIST} tools'))
        return []
    tools = tools if tools is not None else ai_workflows_tools_catalogue()
    classification = {t.get('name'): t.get('classification') for t in tools}
    names = []
    seen = set()
    for name in allowlist:
        if name in seen:
            continue
        seen.add(name)
        if name not in classification:
            errors.append(_error(None, 'write_tool_allowlist', f'Unknown tool {name}'))
        elif classification[name] != 'write':
            errors.append(_error(None, 'write_tool_allowlist',
                                 f'{name} is a read tool; the allowlist only holds write tools'))
        names.append(name)
    return names


def _validate_owner(body, existing, user_id, is_admin, errors):
    """The owner user row, or raises when a non-admin names someone else."""
    if 'owner_id' in body and body.get('owner_id') is not None:
        raw = body.get('owner_id')
        try:
            owner_id = int(raw)
        except (TypeError, ValueError):
            errors.append(_error(None, 'owner_id', 'owner_id must be a user id'))
            return None
        current = existing.owner_id if existing is not None else user_id
        if owner_id != current and not is_admin:
            raise AiWorkflowsForbiddenError('Only administrators may set the owner of a workflow to another user')
    else:
        owner_id = existing.owner_id if existing is not None else user_id
    owner = ai_workflows_db_get_user(owner_id)
    if owner is None or owner.active is False:
        errors.append(_error(None, 'owner_id', 'The owner must be an existing, active user'))
        return None
    return owner


def _errors_on(errors, *fields) -> bool:
    return any(e.get('field') in fields for e in errors)


def _body_triggers(body, current, errors):
    """The triggers [{type, config}] the body sets: its `triggers`, else
    its `trigger_type` / `trigger_config` replacing the first of
    `current`; None when it sets none."""
    if body.get('triggers') is not None:
        triggers = body.get('triggers')
        if not isinstance(triggers, list) or not all(isinstance(t, dict) for t in triggers):
            errors.append(_error(None, 'triggers', 'triggers must be a list of {type, config} objects'))
            return None
    elif 'trigger_type' in body or 'trigger_config' in body:
        first = current[0] if current else {}
        trigger_type = body.get('trigger_type', first.get('type'))
        same_type = trigger_type == first.get('type')
        config = body.get('trigger_config') if 'trigger_config' in body else (first.get('config') if same_type
                                                                              else {})
        if trigger_type not in TRIGGER_TYPES:
            errors.append(_error(None, 'trigger_type', f'trigger_type must be one of {", ".join(TRIGGER_TYPES)}'))
            return None
        if config is not None and not isinstance(config, dict):
            errors.append(_error(None, 'trigger_config', 'trigger_config must be an object'))
            return None
        triggers = [{'type': trigger_type, 'config': config or {}}] + list(current[1:])
    else:
        return None
    for trigger in triggers:
        config = trigger.get('config') if trigger.get('config') is not None else {}
        if not isinstance(config, dict):
            errors.append(_error(None, 'triggers', 'A trigger config must be an object'))
            return None
    return [{'type': t.get('type'), 'config': t.get('config') or {}} for t in triggers]


def _carries_triggers(graph) -> bool:
    return any(ai_workflow_trigger_of_node(node) is not None for node in graph.get('nodes') or []
               if isinstance(node, dict) and node.get('type') == 'trigger')


def _set_trigger_nodes(graph, triggers) -> dict:
    """The graph with `triggers` set on its trigger nodes, in order."""
    graph = copy.deepcopy(graph)
    remaining = iter(triggers)
    for node in graph.get('nodes') or []:
        if isinstance(node, dict) and node.get('type') == 'trigger':
            trigger = next(remaining)
            node['config'] = {'trigger_type': trigger['type'], **trigger['config']}
    return graph


def _pick_graph(body, existing, errors):
    """The graph to save, each trigger node carrying its trigger; None
    when the graph is malformed. Before trigger nodes carried their
    trigger, the workflow had `triggers` (or `trigger_type` /
    `trigger_config`): a graph without, its single trigger node is
    upgraded with those (of the body, else of `existing`). On a graph
    carrying its triggers, the body may still set them: in node order
    without a graph, and matching the graph's with one."""
    if 'graph' in body:
        graph = body.get('graph')
    else:
        graph = existing.graph if existing is not None else None
    graph = graph or {'nodes': [], 'edges': []}
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes', []), list) \
            or not isinstance(graph.get('edges', []), list):
        errors.append(_error(None, 'graph', 'graph must be an object with "nodes" and "edges" lists'))
        return None
    if not _carries_triggers(graph):
        current = ai_workflow_triggers(existing) if existing is not None else []
        triggers = _body_triggers(body, current, errors)
        return ai_workflows_graph_upgrade_triggers(graph, current if triggers is None else triggers)
    derived = [{'type': t['type'], 'config': t['config']} for t in ai_workflow_triggers({'graph': graph})]
    triggers = _body_triggers(body, derived, errors)
    if triggers is None or triggers == derived:
        return graph
    if 'graph' in body:
        errors.append(_error(None, 'triggers', 'The triggers are set on the trigger nodes of the graph'))
    elif len(triggers) != len(ai_workflows_graph_trigger_ids(graph)):
        errors.append(_error(None, 'triggers', f'One trigger per trigger node of the graph '
                                               f'({len(ai_workflows_graph_trigger_ids(graph))})'))
    else:
        graph = _set_trigger_nodes(graph, triggers)
    return graph


def _graph_triggers(graph) -> list:
    """[{type, config, node_id}] the trigger nodes of `graph` carry."""
    return ai_workflow_triggers({'graph': graph}) if graph is not None and _carries_triggers(graph) else []


def _ai_workflows_validate(body, user_id, is_admin, existing=None) -> dict:
    """Attributes to set on the workflow; raises BusinessProcessingError
    (`data.errors` = [{node_id, field, message}]) or
    AiWorkflowsForbiddenError."""
    if not isinstance(body, dict):
        _raise_invalid([_error(None, None, 'The body must be a JSON object')])
    errors = []

    def pick(field, default):
        if field in body:
            return body.get(field)
        if existing is not None:
            return getattr(existing, field)
        return default

    name = pick('name', None)
    if not isinstance(name, str) or not name.strip():
        errors.append(_error(None, 'name', 'name is required'))
        name = ''
    elif len(name.strip()) > 255:
        errors.append(_error(None, 'name', 'name is at most 255 characters'))
    name = name.strip()

    description = pick('description', None)
    if description is not None and not isinstance(description, str):
        errors.append(_error(None, 'description', 'description must be a string'))
        description = None
    elif description is not None and len(description) > _MAX_DESCRIPTION:
        errors.append(_error(None, 'description', f'description is at most {_MAX_DESCRIPTION} characters'))

    is_active = pick('is_active', False)
    if not isinstance(is_active, bool):
        errors.append(_error(None, 'is_active', 'is_active must be a boolean'))
        is_active = False

    graph = _pick_graph(body, existing, errors)

    allowlist = _validate_allowlist(pick('write_tool_allowlist', []), errors)

    max_runs = _as_int(pick('max_runs_per_hour', None), 'max_runs_per_hour', errors, 1, _MAX_RUNS_PER_HOUR, 60)
    budget = _as_int(pick('token_budget_per_run', None), 'token_budget_per_run', errors, 1, _MAX_TOKEN_BUDGET,
                     50000)

    audience = pick('suggestion_audience', AUDIENCE_ENTITY) or AUDIENCE_ENTITY
    if audience not in AUDIENCES:
        errors.append(_error(None, 'suggestion_audience', f'suggestion_audience must be one of {", ".join(AUDIENCES)}'))

    owner = _validate_owner(body, existing, user_id, is_admin, errors)
    scope = _validate_customer_scope(pick('customer_scope', None), owner, errors)

    if graph is not None and not _errors_on(errors, 'triggers', 'trigger_type', 'trigger_config'):
        errors.extend(ai_workflows_graph_validate(graph, allowlist))

    if errors:
        _raise_invalid(errors)

    # Derived from the trigger nodes: the triggers find the workflows of a
    # type without loading their graphs
    triggers = _graph_triggers(graph)
    return {
        'name': name,
        'description': description,
        'is_active': is_active,
        'triggers': triggers,
        'trigger_type': triggers[0]['type'],
        'trigger_config': triggers[0]['config'],
        'customer_scope': scope,
        'graph': graph,
        'owner_id': owner.id,
        'write_tool_allowlist': allowlist,
        'max_runs_per_hour': max_runs,
        'token_budget_per_run': budget,
        'suggestion_audience': audience,
    }


def ai_workflows_check_python(body) -> dict:
    """Editor check of a `python` node script: `{errors: [{line, message}]}`,
    the static errors the sandbox would refuse it with (empty: it may run)."""
    code = body.get('code') if isinstance(body, dict) else None
    if not isinstance(code, str):
        raise BusinessProcessingError('Invalid body', data={'code': ['Must be a string']})
    return {'errors': ai_workflows_sandbox_check(code)}


def ai_workflows_validate_definition(body) -> dict:
    """Editor dry validation: `{valid, errors}`, never raises on content."""
    body = body if isinstance(body, dict) else {}
    errors = []
    graph = _pick_graph(body, None, errors)
    allowlist = _validate_allowlist(body.get('write_tool_allowlist') or [], errors)
    if graph is not None and not _errors_on(errors, 'triggers', 'trigger_type', 'trigger_config'):
        errors.extend(ai_workflows_graph_validate(graph, allowlist))
    errors = _dedup_errors(errors)
    return {'valid': not errors, 'errors': errors}


# ---- CRUD ------------------------------------------------------------------

def _write_version(workflow, user_id, note):
    ai_workflows_db_add(AiWorkflowVersion(
        workflow_id=workflow.id,
        version=workflow.version,
        snapshot=_definition(workflow),
        note=note if isinstance(note, str) and note.strip() else None,
        created_by_id=user_id,
    ))


def ai_workflows_list(user_id, is_admin) -> list:
    """The workflows the user owns or that are shared with one of their
    groups (administrators: every workflow)."""
    user_group_ids = [] if is_admin else ai_workflows_db_user_group_ids(user_id)
    if is_admin:
        workflows = ai_workflows_db_list()
    else:
        workflows = ai_workflows_db_list(owner_id=user_id, group_ids=user_group_ids)
    ids = [w.id for w in workflows]
    counts = ai_workflows_db_run_counts(ids, ai_workflows_db_utcnow() - datetime.timedelta(hours=24))
    users = ai_workflows_db_user_summary([w.owner_id for w in workflows])
    shared = {i for w in workflows for i in (w.shared_group_ids or [])}
    groups = ai_workflows_db_group_names(shared) if shared else {}
    # No graph in the listing: GET /<id> has it
    return [ai_workflows_serialize(w, counts.get(w.id, {}), users, with_graph=False, groups=groups,
                                   can_edit=_can_edit(w, user_id, is_admin, user_group_ids),
                                   can_manage=_can_manage(w, user_id, is_admin)) for w in workflows]


def ai_workflows_get(workflow_id, user_id, is_admin) -> dict:
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    counts = ai_workflows_db_run_counts([workflow.id], ai_workflows_db_utcnow() - datetime.timedelta(hours=24))
    return ai_workflows_serialize(workflow, counts.get(workflow.id, {}),
                                  can_edit=_can_edit(workflow, user_id, is_admin),
                                  can_manage=_can_manage(workflow, user_id, is_admin))


def ai_workflows_create(body, user_id, is_admin) -> dict:
    attributes = _ai_workflows_validate(body, user_id, is_admin)
    workflow = AiWorkflow(**attributes)
    workflow.version = 1
    workflow.created_by_id = user_id
    ai_workflows_db_add(workflow)
    _write_version(workflow, user_id, (body or {}).get('version_note') or 'Created')
    ai_workflows_db_commit()
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" created', ctx_less=True)
    return ai_workflows_serialize(workflow, {})


def _comparable(value):
    return sorted(set(value)) if isinstance(value, list) else value


def _check_editor_change(workflow, attributes, user_id):
    """An editor who does not manage the workflow changes its graph, never
    the limits its owner set (the automatic runs act as the owner), nor
    adds a keystore reference they could not use themselves."""
    locked = [f for f in _OWNER_FIELDS if _comparable(attributes.get(f)) != _comparable(getattr(workflow, f))]
    if locked:
        raise AiWorkflowsForbiddenError(f'Only the owner of this workflow can change {", ".join(locked)}')
    before = set(ai_workflows_portable_key_references(((workflow.graph or {}).get('nodes'))))
    added = set(ai_workflows_portable_key_references(((attributes.get('graph') or {}).get('nodes')))) - before
    if added:
        usable = {e.name for e in ai_workflows_keystore_visible_entries(user_id)}
        denied = sorted(added - usable)
        if denied:
            raise AiWorkflowsForbiddenError(f'Keystore entry {", ".join(denied)} does not exist or is not usable '
                                            'by you')


def ai_workflows_update(workflow_id, body, user_id, is_admin) -> dict:
    """Owner, administrator or editor; an editor within the limits of
    `_check_editor_change`."""
    workflow = _get_editable_workflow(workflow_id, user_id, is_admin)
    before = _definition(workflow)
    expected = (body or {}).get('expected_revision')
    if expected is not None and not isinstance(expected, str):
        raise BusinessProcessingError('Invalid expected_revision', data={'expected_revision': ['Not a string']})
    if expected is not None and expected != _revision(before):
        raise AiWorkflowsConflictError(_revision(before), workflow.version)
    attributes = _ai_workflows_validate(body, user_id, is_admin, existing=workflow)
    if not _can_manage(workflow, user_id, is_admin):
        _check_editor_change(workflow, attributes, user_id)
    for key, value in attributes.items():
        setattr(workflow, key, value)
    if ai_workflow_trigger_config(workflow, TRIGGER_WEBHOOK) is None:
        workflow.inbound_token_hash = None
        workflow.inbound_signing_secret = None
    after = _definition(workflow)
    changed = [f for f in _VERSIONED_FIELDS if before.get(f) != after.get(f)]
    if changed:
        workflow.version = (workflow.version or 1) + 1
        _write_version(workflow, user_id, (body or {}).get('version_note'))
    ai_workflows_db_commit()
    suffix = f' (version {workflow.version}: {", ".join(changed)})' if changed else ''
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" updated{suffix}', ctx_less=True)
    if after != before:
        _emit_saved(workflow.id, user_id, {'version': workflow.version, 'revision': _revision(after),
                                           'editor_client_id': _editor_client_id(body)})
    return ai_workflows_get(workflow.id, user_id, is_admin)


def _editor_client_id(body):
    """The editor tab a save came from, echoed to the open editors so
    that tab recognises its own save."""
    value = (body or {}).get('editor_client_id')
    if isinstance(value, str) and 0 < len(value) <= 64 and value.replace('-', '').isalnum():
        return value
    return None


def _emit_saved(workflow_id, user_id, payload):
    user = _user_summary(user_id, None) or {}
    ai_workflows_live_emit_saved(workflow_id, {'user_id': user_id, 'user_name': user.get('name'), **payload})


def ai_workflows_set_category(workflow_id, category_id, user_id, is_admin) -> dict:
    """File the workflow under an AI-workflow category, or none (`None`).
    Not a definition change: no new version."""
    workflow = _get_owned_workflow(workflow_id, user_id, is_admin)
    category_id = categories_resolve_id(CATEGORY_KIND_AI_WORKFLOW, category_id)
    if workflow.category_id != category_id:
        workflow.category_id = category_id
        ai_workflows_db_commit()
        target = f'category "{workflow.category.name}"' if workflow.category else 'no category'
        track_activity(f'AI workflow #{workflow.id} "{workflow.name}" moved to {target}', ctx_less=True)
    return ai_workflows_get(workflow.id, user_id, is_admin)


def _validate_shared_group_ids(group_ids, field='group_ids') -> list:
    if not isinstance(group_ids, list) or len(group_ids) > _MAX_SHARED_GROUPS \
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in group_ids):
        raise BusinessProcessingError('Invalid sharing', data={field: ['Must be a list of group ids']})
    group_ids = sorted(set(group_ids))
    missing = sorted(set(group_ids) - set(ai_workflows_db_existing_group_ids(group_ids)))
    if missing:
        raise BusinessProcessingError('Invalid sharing', data={
            'group_ids': [f'Unknown group ids: {", ".join(str(i) for i in missing)}']})
    return group_ids


def ai_workflows_set_shared_groups(workflow_id, group_ids, user_id, is_admin, edit_group_ids=None) -> dict:
    """Share the workflow with the members of `group_ids` (none: private
    again). They may see it and run it manually, as themselves; those of
    `edit_group_ids` (a subset; None keeps the current editors still
    shared with) may also edit it, within the limits its owner set. Not a
    definition change: no new version."""
    workflow = _get_owned_workflow(workflow_id, user_id, is_admin)
    group_ids = _validate_shared_group_ids(group_ids)
    if edit_group_ids is None:
        edit_group_ids = [i for i in (workflow.shared_edit_group_ids or []) if i in group_ids]
    edit_group_ids = sorted(set(edit_group_ids)) if isinstance(edit_group_ids, list) else edit_group_ids
    edit_group_ids = _validate_shared_group_ids(edit_group_ids, 'edit_group_ids')
    if set(edit_group_ids) - set(group_ids):
        raise BusinessProcessingError('Invalid sharing', data={
            'edit_group_ids': ['The workflow must be shared with every group that may edit it']})
    if list(workflow.shared_group_ids or []) != group_ids \
            or list(workflow.shared_edit_group_ids or []) != edit_group_ids:
        workflow.shared_group_ids = group_ids
        workflow.shared_edit_group_ids = edit_group_ids
        ai_workflows_db_commit()
        names = ai_workflows_db_group_names(group_ids)
        target = ', '.join(f'"{names.get(i, i)}"{" (edit)" if i in edit_group_ids else ""}'
                           for i in group_ids) if group_ids else 'no group'
        track_activity(f'AI workflow #{workflow.id} "{workflow.name}" shared with {target}', ctx_less=True)
    return ai_workflows_get(workflow.id, user_id, is_admin)


def ai_workflows_share_groups() -> list:
    """`[{id, name}]` of the groups a workflow can be shared with."""
    return [{'id': group_id, 'name': name} for group_id, name in ai_workflows_db_group_names().items()]


def ai_workflows_delete(workflow_id, user_id, is_admin) -> None:
    """Deactivate under the workflow lock (no trigger can start a run
    past this point), cancel the active runs, then delete."""
    workflow = _get_owned_workflow(workflow_id, user_id, is_admin)
    name = workflow.name
    workflow = ai_workflows_db_lock_workflow(workflow.id)
    if workflow is None:
        raise ObjectNotFoundError()
    workflow.is_active = False
    ai_workflows_db_commit()
    for run in ai_workflows_db_active_runs_for_workflow(workflow.id):
        ai_workflows_engine_cancel_run(run, user_id, reason='Workflow deleted')
    ai_workflows_db_delete(workflow)
    track_activity(f'AI workflow #{workflow_id} "{name}" deleted', ctx_less=True)
    _emit_saved(workflow_id, user_id, {'deleted': True})


def ai_workflows_export(workflow_id, user_id, is_admin) -> dict:
    """The portable JSON document of a workflow: no owner, customer
    scope, inbound token, signing secret or keystore value; a literal
    credential left in an HTTP request becomes a `key()` reference."""
    workflow = _get_owned_workflow(workflow_id, user_id, is_admin)
    document = ai_workflows_portable_export_workflow(_definition(workflow),
                                                     exported_at=_iso(ai_workflows_db_utcnow()),
                                                     instance_version=app.config.get('IRIS_VERSION'))
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" exported', ctx_less=True)
    return document


def ai_workflows_import_warnings(user_id, nodes, allowlist=()) -> list:
    """What an imported definition needs and the importer lacks:
    keystore entries it cannot use, tools disabled here, literal secrets."""
    warnings = []
    visible = {e.name for e in ai_workflows_keystore_visible_entries(user_id)}
    for name in ai_workflows_portable_key_references(nodes):
        if name not in visible:
            warnings.append(_error(None, 'keystore', f'Keystore entry {name} does not exist or is not usable by '
                                                     'you: create it before activating the workflow'))
    enabled = {t['name']: t.get('enabled', True) for t in ai_workflows_tools_catalogue()}
    for name in ai_workflows_portable_tools(nodes, allowlist):
        if name in enabled and not enabled[name]:
            warnings.append(_error(None, 'tools', f'Tool {name} is disabled in the MCP settings of this instance'))
    for warning in ai_workflows_portable_literal_secrets(nodes):
        warnings.append(_error(warning['node_id'], warning['field'], 'This field holds a literal credential: '
                                                                     'move it to the keystore and use key()'))
    return warnings


def ai_workflows_import(document, user_id, is_admin) -> dict:
    """Create a workflow from a portable document (or a bare definition).
    The workflow is inactive, owned by the importer, without customer
    scope; it is validated like any new workflow."""
    try:
        body = ai_workflows_portable_read_workflow(document)
    except PortableError as e:
        _raise_invalid([_error(None, None, str(e))])
    body['is_active'] = False
    body['version_note'] = 'Imported'
    workflow = ai_workflows_create(body, user_id, is_admin)
    graph = body.get('graph') if isinstance(body.get('graph'), dict) else {}
    warnings = ai_workflows_import_warnings(user_id, graph.get('nodes'), body.get('write_tool_allowlist'))
    return {'workflow': workflow, 'warnings': warnings}


def ai_workflows_list_versions(workflow_id, user_id, is_admin) -> list:
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    versions = ai_workflows_db_list_versions(workflow.id)
    users = ai_workflows_db_user_summary([v.created_by_id for v in versions])
    return [{
        'version': v.version,
        'note': v.note,
        'created_at': _iso(v.created_at),
        'created_by_id': v.created_by_id,
        'created_by': users.get(v.created_by_id),
    } for v in versions]


def ai_workflows_get_version(workflow_id, version, user_id, is_admin) -> dict:
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    row = ai_workflows_db_get_version(workflow.id, version)
    if row is None:
        raise ObjectNotFoundError()
    return {
        'workflow_id': workflow.id,
        'version': row.version,
        'note': row.note,
        'created_at': _iso(row.created_at),
        'created_by_id': row.created_by_id,
        'created_by': _user_summary(row.created_by_id),
        'snapshot': _upgraded_snapshot(row.snapshot),
    }


def _upgraded_snapshot(snapshot):
    """A version saved before trigger nodes carried their trigger, its
    graph upgraded (see `_definition`)."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('graph'), dict):
        return snapshot
    graph = ai_workflows_graph_upgrade_triggers(snapshot['graph'], ai_workflow_triggers(snapshot))
    return {**snapshot, 'graph': graph, 'triggers': _graph_triggers(graph) or ai_workflow_triggers(snapshot)}


def _new_signing_secret(workflow) -> str:
    """A fresh HMAC key for the webhook signatures, stored encrypted."""
    secret = ai_workflows_engine_new_token()
    workflow.inbound_signing_secret = encrypt_secret(secret)
    return secret


def _get_webhook_workflow(workflow_id, user_id, is_admin) -> AiWorkflow:
    workflow = _get_owned_workflow(workflow_id, user_id, is_admin)
    if ai_workflow_trigger_config(workflow, TRIGGER_WEBHOOK) is None:
        raise BusinessProcessingError('Only webhook-triggered workflows have an inbound token')
    return workflow


def ai_workflows_rotate_inbound_token(workflow_id, user_id, is_admin) -> dict:
    """New bearer token AND new signing secret, both shown once."""
    workflow = _get_webhook_workflow(workflow_id, user_id, is_admin)
    token = ai_workflows_engine_new_token()
    workflow.inbound_token_hash = ai_workflows_engine_hash_token(token)
    signing_secret = _new_signing_secret(workflow)
    ai_workflows_db_commit()
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" inbound token and signing secret rotated',
                   ctx_less=True)
    return {'token': token, 'signing_secret': signing_secret,
            'url': ai_workflows_engine_inbound_url(str(workflow.uuid))}


def ai_workflows_rotate_signing_secret(workflow_id, user_id, is_admin) -> dict:
    """New signing secret only (the bearer token is kept), shown once."""
    workflow = _get_webhook_workflow(workflow_id, user_id, is_admin)
    signing_secret = _new_signing_secret(workflow)
    ai_workflows_db_commit()
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" inbound signing secret rotated', ctx_less=True)
    return {'signing_secret': signing_secret}


# ---- Catalogue -------------------------------------------------------------

def _callback_base_url():
    return (app.config.get('AI_WORKFLOWS_CALLBACK_BASE_URL') or app.config.get('IRIS_ALLOW_ORIGIN') or '').rstrip('/')


def _llm_status() -> dict:
    """Never the API key: only whether a provider is usable."""
    try:
        config = load_config(None)
    except Exception:
        return {'enabled': False, 'provider': None, 'model': None}
    return {
        'enabled': bool(config.enabled),
        'provider': config.provider or None,
        'model': config.model or None,
    }


def ai_workflows_catalogue(user_id) -> dict:
    keystore = [{
        'name': e.name,
        'is_secret': bool(e.is_secret),
        'scope': e.scope,
    } for e in ai_workflows_keystore_visible_entries(user_id)]
    return {
        'node_types': ai_workflows_nodes_catalogue(),
        'tools': ai_workflows_tools_catalogue(),
        'hooks': [{'name': name, 'description': description}
                  for name, description in ai_workflows_db_postload_hooks()],
        'trigger_types': list(TRIGGER_TYPES),
        'entity_types': list(ENTITY_TYPES),
        'suggestion_kinds': list(SUGGESTION_KINDS),
        'suggestion_audiences': list(AUDIENCES),
        'keystore': keystore,
        'callback_base_url': _callback_base_url(),
        'enabled': bool(app.config.get('AI_WORKFLOWS_ENABLED', True)),
        'llm': _llm_status(),
    }


def ai_workflows_authoring_guide(user_id) -> dict:
    """`{markdown, examples}`: how to write workflows and blocks as JSON,
    with the live catalogue of this instance (keystore names only)."""
    catalogue = ai_workflows_catalogue(user_id)
    examples = ai_workflows_guide_examples()
    return {'markdown': ai_workflows_guide_render(catalogue, examples), 'examples': examples}


# ---- Runs ------------------------------------------------------------------

def ai_workflows_require_enabled():
    """Raise AiWorkflowsDisabledError when `AI_WORKFLOWS_ENABLED` is off."""
    if not app.config.get('AI_WORKFLOWS_ENABLED', True):
        raise AiWorkflowsDisabledError()


def _check_entity(workflow, entity_type, entity_id, user_id, scope_mask=None, any_type=False, trigger=None):
    """Validate and coerce a run target the clicking user must be able
    to access (an entity they cannot see is not found); returns
    (type, id). `any_type` (a node test) ignores the manual trigger
    restriction; `trigger` is the one the run starts at."""
    # Manual trigger `entity_types` restricts the target, and then makes it
    # mandatory; empty = any type, or none
    allowed_types = list(ENTITY_TYPES)
    restricted = False
    if trigger is not None:
        manual = trigger['config'] if trigger['type'] == TRIGGER_MANUAL else None
    else:
        manual = ai_workflow_trigger_config(workflow, TRIGGER_MANUAL)
    if manual is not None and not any_type:
        configured = list(manual.get('entity_types') or [])
        if configured:
            allowed_types = configured
            restricted = True
    if entity_type in (None, '') and entity_id in (None, ''):
        if restricted:
            raise BusinessProcessingError('This workflow runs on an entity',
                                          data={'entity_type': [f'One of {", ".join(allowed_types)}']})
        return None, None
    if entity_type not in ENTITY_TYPES or entity_type not in allowed_types:
        raise BusinessProcessingError('This workflow cannot run on this entity type',
                                      data={'entity_type': [f'Allowed: {", ".join(allowed_types) or "none"}']})
    if isinstance(entity_id, bool):
        entity_id = None
    try:
        entity_id = int(entity_id)
    except (TypeError, ValueError):
        raise BusinessProcessingError('Invalid entity id', data={'entity_id': ['An integer id is required']})
    _check_entity_access(user_id, entity_type, entity_id, scope_mask)
    scope = workflow.customer_scope or []
    if scope:
        customer_id = ai_workflows_db_entity_customer(entity_type, entity_id)
        if customer_id is not None and customer_id not in scope:
            raise BusinessProcessingError('The entity belongs to a customer outside the workflow scope')
    return entity_type, entity_id


def _check_entity_access(user_id, entity_type, entity_id, scope_mask):
    """The clicking user (bounded by their API-key scope) must see the
    entity; missing and inaccessible are the same 404."""
    if not entity_type or entity_id is None:
        return
    if not ai_workflows_db_entity_exists(entity_type, entity_id) \
            or not _entity_visible(user_id, entity_type, entity_id, scope_mask=scope_mask):
        raise ObjectNotFoundError()


def _manual_trigger(workflow, node_id):
    """The trigger an editor run starts at: the one of trigger node
    `node_id`, else the first manual trigger, else the first trigger."""
    triggers = ai_workflow_triggers(workflow)
    if node_id is None:
        return next((t for t in triggers if t['type'] == TRIGGER_MANUAL), triggers[0] if triggers else None)
    trigger = next((t for t in triggers if t['node_id'] == node_id), None) if isinstance(node_id, str) else None
    if trigger is None:
        raise BusinessProcessingError('Invalid run request',
                                      data={'trigger_node_id': ['Not a trigger node of the workflow']})
    return trigger


def _start_run(workflow, trigger_type, user_id, is_admin, **kwargs):
    """A run started by `user_id`: acting as the workflow owner when the
    user is the owner or an administrator, else (a member of a group the
    workflow is shared with, editor or not) as the user, like a run from
    an object menu."""
    if _can_manage(workflow, user_id, is_admin):
        return ai_workflows_engine_start_run(workflow, trigger_type, run_as_user_id=workflow.owner_id,
                                             triggered_by_user_id=user_id, **kwargs)
    denial = ai_workflows_engine_access_denial(workflow, user_id, kwargs.get('entity_type'),
                                               kwargs.get('entity_id'), kwargs.get('scope_mask'))
    return ai_workflows_engine_insert_run(workflow, trigger_type, denial=denial, run_as_user_id=user_id,
                                          triggered_by_user_id=user_id, **kwargs)


def ai_workflows_run_manual(workflow_id, body, user_id, is_admin, scope_mask=None) -> dict:
    """Manual run (or dry run), at the trigger node `trigger_node_id` of
    the body (see `_manual_trigger`). It acts as the workflow owner, or
    as the clicking user when the workflow is only shared with them (see
    `_start_run`), is recorded as triggered by the clicking user, who must
    access the entity, and is bounded by the clicking user's API-key
    scope."""
    ai_workflows_require_enabled()
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    body = body if isinstance(body, dict) else {}
    trigger = _manual_trigger(workflow, body.get('trigger_node_id'))
    entity_type, entity_id = _check_entity(workflow, body.get('entity_type'), body.get('entity_id'), user_id,
                                           scope_mask, trigger=trigger)
    dry_run = body.get('dry_run', False)
    if not isinstance(dry_run, bool):
        raise BusinessProcessingError('Invalid run request', data={'dry_run': ['Must be a boolean']})
    payload = body.get('payload')
    if payload is not None and not isinstance(payload, (dict, list)):
        raise BusinessProcessingError('Invalid run request', data={'payload': ['Must be an object or a list']})
    run = _start_run(
        workflow, TRIGGER_MANUAL, user_id, is_admin,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        dry_run=dry_run,
        scope_mask=scope_mask,
        trigger_node_id=trigger['node_id'] if trigger is not None else None,
    )
    target = f' on {entity_type} #{entity_id}' if entity_type else ''
    mode = 'dry run' if dry_run else 'run'
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" {mode} started manually{target} '
                   f'(run {run.uuid})', ctx_less=True)
    return ai_workflows_run_summary(run)


def ai_workflows_rerun(run_uuid, user_id, is_admin, scope_mask=None) -> dict:
    """Same trigger payload, new run, triggered by the current user, who
    must access the entity; it acts as the workflow owner, or as the
    current user when the workflow is only shared with them."""
    ai_workflows_require_enabled()
    original = _get_visible_run(run_uuid, user_id, is_admin, scope_mask)
    if original.workflow_id is None:
        raise BusinessProcessingError('The workflow of this run has been deleted')
    workflow = _get_visible_workflow(original.workflow_id, user_id, is_admin)
    _check_entity_access(user_id, original.entity_type, original.entity_id, scope_mask)
    run = _start_run(
        workflow, original.trigger_type, user_id, is_admin,
        entity_type=original.entity_type,
        entity_id=original.entity_id,
        sub_entity=original.sub_entity,
        payload=original.trigger_payload,
        dry_run=bool(original.is_dry_run),
        chain_depth=original.chain_depth or 0,
        parent_run_id=original.parent_run_id,
        scope_mask=scope_mask,
        trigger_node_id=((original.context or {}).get('trigger') or {}).get('node_id'),
    )
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" run {original.uuid} re-run as {run.uuid}',
                   ctx_less=True)
    return ai_workflows_run_summary(run)


def ai_workflows_cancel(run_uuid, user_id, is_admin, scope_mask=None) -> dict:
    run = _get_visible_run(run_uuid, user_id, is_admin, scope_mask)
    if run.status not in RUN_ACTIVE_STATUSES:
        raise BusinessProcessingError(f'The run is already {run.status}')
    run = ai_workflows_engine_cancel_run(run, user_id, reason='Cancelled by user')
    track_activity(f'AI workflow run {run.uuid} ("{run.workflow_name}") cancelled', ctx_less=True)
    return ai_workflows_run_summary(run)


def _visible_page(user_id, scope_mask, filters, page, per_page) -> tuple:
    """(page rows, total) of the runs a non-administrator can see: the
    runs they are involved in, then the entity access check, so the
    total counts exactly what can be paged through."""
    candidates = ai_workflows_business_db_involved_runs(user_id, limit=_MAX_RUN_SCAN, **filters)
    cache = {}
    visible = [c.id for c in candidates
               if _entity_visible(user_id, c.entity_type, c.entity_id, cache, scope_mask)]
    start = (page - 1) * per_page
    return ai_workflows_business_db_runs_by_ids(visible[start:start + per_page]), len(visible)


def ai_workflows_list_runs(user_id, is_admin, workflow_id=None, status=None, entity_type=None, entity_id=None,
                           page=1, per_page=25, scope_mask=None) -> dict:
    if status and status not in RUN_STATUSES:
        raise BusinessProcessingError('Invalid status filter', data={'status': [f'Unknown status {status}']})
    if entity_type and entity_type not in ENTITY_TYPES:
        raise BusinessProcessingError('Invalid entity type filter',
                                      data={'entity_type': [f'Unknown entity type {entity_type}']})
    per_page = max(1, min(int(per_page or 25), _MAX_PER_PAGE))
    page = max(1, int(page or 1))
    filters = {'workflow_id': workflow_id, 'status': status or None, 'entity_type': entity_type or None,
               'entity_id': entity_id}
    if is_admin:
        rows, total = ai_workflows_db_list_runs(user_id=None, page=page, per_page=per_page, **filters)
    else:
        rows, total = _visible_page(user_id, scope_mask, filters, page, per_page)
    users = ai_workflows_db_user_summary([r.run_as_user_id for r in rows] +
                                         [r.triggered_by_user_id for r in rows])
    titles = {}
    last_page = max(1, -(-total // per_page)) if total > 0 else 1
    return {
        'data': [ai_workflows_run_summary(r, users, titles) for r in rows],
        'total': total,
        'page': page,
        'per_page': per_page,
        'current_page': page,
        'last_page': last_page,
        'next_page': page + 1 if page < last_page else None,
    }


def _run_detail(run, user_id, is_admin, everything=False) -> dict:
    """`everything` (export) skips the suggestion visibility filter; what
    another analyst resolved stays hidden either way."""
    privileged = everything or _run_privileged(user_id, is_admin, run)
    steps = ai_workflows_db_list_steps(run.id)
    tool_calls, omitted = _cap_tool_calls(ai_workflows_db_list_tool_calls(run.id))
    llm_calls = ai_workflows_db_list_llm_calls(run.id)
    waits = ai_workflows_db_list_waits(run.id)
    suggestions = ai_workflows_db_list_suggestions(run_id=run.id, limit=1000)
    if not everything:
        suggestions = [s for s in suggestions if ai_workflows_suggestions_user_can_see(user_id, s)]
    users = ai_workflows_db_user_summary([run.run_as_user_id, run.triggered_by_user_id] +
                                         [c.acting_user_id for c in tool_calls] +
                                         [w.resolved_by_id for w in waits])
    data = ai_workflows_run_summary(run, users)
    data.update({
        'trigger_payload': run.trigger_payload,
        'context': run.context,
        'definition_snapshot': run.definition_snapshot,
        'customer_id': run.customer_id,
        'can_view_llm_snapshots': privileged,
        'steps': [_step_public(s) for s in steps],
        'tool_calls': [_tool_call_public(c, users, user_id) for c in tool_calls],
        'tool_calls_omitted': omitted,
        'llm_calls': [_llm_call_public(c, privileged) for c in llm_calls],
        'waits': [_wait_public(w, users, user_id) for w in waits],
        'suggestions': [ai_workflows_suggestion_public(s, user_id) for s in suggestions],
    })
    return data


def ai_workflows_get_run(run_uuid, user_id, is_admin, scope_mask=None) -> dict:
    run = _get_visible_run(run_uuid, user_id, is_admin, scope_mask)
    return _run_detail(run, user_id, is_admin)


def ai_workflows_export_run(run_uuid, user_id, is_admin, scope_mask=None) -> dict:
    run = _get_visible_run(run_uuid, user_id, is_admin, scope_mask)
    if not _run_privileged(user_id, is_admin, run):
        raise AiWorkflowsForbiddenError('Only administrators and the workflow owner may export a run')
    data = _run_detail(run, user_id, is_admin, everything=True)
    data['inbound_events'] = [ai_workflows_inbound_event_public(e)
                              for e in ai_workflows_db_list_inbound_events_for_run(run.id)]
    data['exported_at'] = _iso(ai_workflows_db_utcnow())
    data['exported_by'] = _user_summary(user_id)
    track_activity(f'AI workflow run {run.uuid} ("{run.workflow_name}") exported', ctx_less=True)
    return data


def ai_workflows_list_inbound_events(user_id, is_admin, workflow_id=None, limit=100) -> list:
    limit = max(1, min(int(limit or 100), 500))
    if workflow_id is not None:
        workflow = _get_owned_workflow(workflow_id, user_id, is_admin)
        events = ai_workflows_db_list_inbound_events(workflow_id=workflow.id, limit=limit)
    elif is_admin:
        events = ai_workflows_db_list_inbound_events(limit=limit)
    else:
        events = ai_workflows_db_list_inbound_events_for_workflows(ai_workflows_db_owned_workflow_ids(user_id),
                                                                   limit=limit)
    run_uuids = ai_workflows_db_run_uuids([e.run_id for e in events])
    return [ai_workflows_inbound_event_public(e, run_uuids) for e in events]


# ---- Node events -----------------------------------------------------------
# Every execution of a node (a step, `resumed` rows aside) is an event the
# node processed; any of them can be replayed through the current
# definition of the workflow, from that node, with the context it saw.

_EVENT_STATUSES = (STEP_SUCCEEDED, STEP_FAILED, STEP_WAITING)


def _socket_user(user_id):
    """(user, is_admin) of a Socket.IO caller holding an AI-workflow
    permission; raises as the REST views do otherwise."""
    ai_workflows_require_enabled()
    user = ai_workflows_db_get_user(user_id) if user_id else None
    if user is None or not user.active:
        raise ObjectNotFoundError()
    permissions = ac_get_effective_permissions_of_user(user)
    is_admin = ac_has_permission_server_administrator(permissions)
    readable = Permissions.ai_workflows_read.value | Permissions.ai_workflows_write.value
    if not is_admin and not permissions & readable:
        raise AiWorkflowsForbiddenError()
    return user, is_admin


def _socket_workflow(workflow_id, user_id, is_admin, owned=True) -> AiWorkflow:
    if isinstance(workflow_id, bool):
        raise ObjectNotFoundError()
    try:
        workflow_id = int(workflow_id)
    except (TypeError, ValueError):
        raise ObjectNotFoundError()
    if owned:
        return _get_owned_workflow(workflow_id, user_id, is_admin)
    return _get_visible_workflow(workflow_id, user_id, is_admin)


def ai_workflows_live_room(user_id, run_uuid=None, workflow_id=None) -> str:
    """The Socket.IO room pushing the live progress of a run (one the
    user can see) or of every run of a workflow (one the user can edit).
    Raises ObjectNotFoundError otherwise, as the REST views do."""
    _, is_admin = _socket_user(user_id)
    if run_uuid:
        return ai_workflows_live_run_room(_get_visible_run(str(run_uuid), user_id, is_admin).uuid)
    if workflow_id is not None:
        return ai_workflows_live_workflow_room(_socket_workflow(workflow_id, user_id, is_admin).id)
    raise BusinessProcessingError('A run or a workflow is required')


def ai_workflows_editor_session(user_id, workflow_id) -> dict:
    """`{room, user_id, user_name}` of a user opening the editor of a
    workflow they can see: the room of its editors, and the identity the
    others are shown — the server's, never the client's."""
    user, is_admin = _socket_user(user_id)
    workflow = _socket_workflow(workflow_id, user_id, is_admin, owned=False)
    return {'room': ai_workflows_live_editor_room(workflow.id), 'workflow_id': workflow.id,
            'user_id': user.id, 'user_name': user.name or user.user}


def ai_workflows_node_stats(workflow_id, user_id, is_admin) -> dict:
    """`{nodes: {node_id: {total, counts: {status: n}, last_at}}}`: the
    events each node processed, over every run of the workflow (for a
    non-administrator, the runs they are involved in)."""
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    stats = ai_workflows_business_db_node_stats(workflow.id, None if is_admin else user_id)
    return {'nodes': {node_id: {'total': sum(entry['counts'].values()), 'counts': entry['counts'],
                                'last_at': _iso(entry['last_at'])}
                      for node_id, entry in stats.items()}}


def ai_workflows_node_events(workflow_id, node_id, user_id, is_admin, status=None, page=1, per_page=25,
                             scope_mask=None) -> dict:
    """The events `node_id` processed, newest first, each with its run;
    a non-administrator only sees the events of runs they can see
    (`ai_workflows_user_can_see_run`: involved in, on an entity they can
    access) — not those of a previous owner of the workflow."""
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    if status and status not in _EVENT_STATUSES:
        raise BusinessProcessingError('Invalid status filter', data={'status': [f'Unknown status {status}']})
    per_page = max(1, min(int(per_page or 25), _MAX_PER_PAGE))
    page = max(1, int(page or 1))
    candidates = ai_workflows_business_db_node_step_candidates(workflow.id, node_id, status or None,
                                                               limit=_MAX_RUN_SCAN,
                                                               involved_user_id=None if is_admin else user_id)
    if not is_admin:
        cache = {}
        candidates = [c for c in candidates if _entity_visible(user_id, c.entity_type, c.entity_id, cache,
                                                               scope_mask)]
    total = len(candidates)
    start = (page - 1) * per_page
    steps = ai_workflows_business_db_steps_by_ids([c.id for c in candidates[start:start + per_page]])
    runs = {r.id: r for r in ai_workflows_business_db_runs_by_ids(list(dict.fromkeys(s.run_id for s in steps)))}
    users = ai_workflows_db_user_summary([r.run_as_user_id for r in runs.values()] +
                                         [r.triggered_by_user_id for r in runs.values()])
    titles = {}
    last_page = max(1, -(-total // per_page)) if total > 0 else 1
    return {
        'node_id': node_id,
        'data': [{**_step_public(s), 'run': ai_workflows_run_summary(runs[s.run_id], users, titles)}
                 for s in steps if s.run_id in runs],
        'total': total,
        'truncated': total >= _MAX_RUN_SCAN,
        'page': page,
        'per_page': per_page,
        'current_page': page,
        'last_page': last_page,
        'next_page': page + 1 if page < last_page else None,
    }


def _get_node_event(workflow, node_id, step_id, user_id, is_admin, scope_mask):
    """(step, run) of an event of `node_id` in a run of `workflow` the
    user can see; anything else is not found."""
    step = ai_workflows_db_get_step(step_id)
    if step is None or step.node_id != node_id or step.status == STEP_RESUMED:
        raise ObjectNotFoundError()
    run = ai_workflows_db_get_run(step.run_id)
    if run is None or run.workflow_id != workflow.id \
            or not ai_workflows_user_can_see_run(user_id, is_admin, run, scope_mask=scope_mask):
        raise ObjectNotFoundError()
    return step, run


def ai_workflows_node_event(workflow_id, node_id, step_id, user_id, is_admin, scope_mask=None) -> dict:
    """One event with the context the node saw (what a replay starts with)."""
    workflow = _get_visible_workflow(workflow_id, user_id, is_admin)
    step, run = _get_node_event(workflow, node_id, step_id, user_id, is_admin, scope_mask)
    data = {**_step_public(step), 'run': ai_workflows_run_summary(run)}
    data['context'] = ai_workflows_engine_replay_context(run, ai_workflows_db_list_steps(run.id), step)
    return data


def ai_workflows_replay_event(workflow_id, node_id, step_id, body, user_id, is_admin, scope_mask=None) -> dict:
    """Replay an event through the current definition of the workflow,
    from its node. Like a re-run, it acts as the workflow owner (as the
    current user for an editor who does not manage it) and is triggered
    by the current user, who must access the entity; it is a dry run if
    asked, or if the replayed run was one."""
    ai_workflows_require_enabled()
    workflow = _get_editable_workflow(workflow_id, user_id, is_admin)
    step, source = _get_node_event(workflow, node_id, step_id, user_id, is_admin, scope_mask)
    if node_id not in ai_workflows_graph_nodes(workflow.graph):
        raise BusinessProcessingError('This node is no longer in the workflow: the event cannot be replayed')
    body = body if isinstance(body, dict) else {}
    dry_run = body.get('dry_run', bool(source.is_dry_run))
    if not isinstance(dry_run, bool):
        raise BusinessProcessingError('Invalid replay request', data={'dry_run': ['Must be a boolean']})
    _check_entity_access(user_id, source.entity_type, source.entity_id, scope_mask)
    run = ai_workflows_engine_replay_step(workflow, source, step, ai_workflows_db_list_steps(source.id),
                                          triggered_by_user_id=user_id, dry_run=dry_run, scope_mask=scope_mask,
                                          run_as_user_id=_run_as(workflow, user_id, is_admin))
    mode = 'dry run' if dry_run else 'run'
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" event of node {node_id} (run {source.uuid}, '
                   f'step {step.id}) replayed as {mode} {run.uuid}', ctx_less=True)
    return ai_workflows_run_summary(run)


# ---- Node tests ------------------------------------------------------------
# One node executed on its own, from its definition in the editor (saved
# or not), so its output can be checked before the workflow runs it.

def _test_context_part(body, name, kinds):
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, kinds):
        raise BusinessProcessingError('Invalid node test', data={name: ['Must be an object']})
    try:
        size = len(json.dumps(value, default=str).encode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        raise BusinessProcessingError('Invalid node test', data={name: ['Not serialisable']})
    if size > _MAX_TEST_CONTEXT_BYTES:
        raise BusinessProcessingError('Invalid node test',
                                      data={name: [f'At most {_MAX_TEST_CONTEXT_BYTES // 1024} KB']})
    return value


def ai_workflows_test_node(workflow_id, body, user_id, is_admin, scope_mask=None) -> dict:
    """Test `body.node` alone: from the context an earlier event of that
    node saw (`step_id`), or from an entity, a trigger payload and the
    upstream outputs / variables the tester supplies (`entity_type`,
    `entity_id`, `payload`, `nodes`, `vars`). The node is checked against
    the workflow write allowlist; like any run, the test acts as the
    workflow owner (as the current user for an editor who does not
    manage it), is triggered by the current user, who must access the
    entity, and really executes the node unless `dry_run`."""
    ai_workflows_require_enabled()
    workflow = _get_editable_workflow(workflow_id, user_id, is_admin)
    run_as_user_id = _run_as(workflow, user_id, is_admin)
    body = body if isinstance(body, dict) else {}
    node = body.get('node')
    errors = ai_workflows_graph_validate_node(node, workflow.write_tool_allowlist)
    if errors:
        raise BusinessProcessingError('Invalid node', data={'errors': errors})
    if node.get('type') == NODE_TRIGGER:
        raise BusinessProcessingError('A trigger cannot be tested on its own: run the workflow')
    if node.get('type') == NODE_NOTE:
        raise BusinessProcessingError('A note is not run, there is nothing to test')
    if ai_workflows_nodes_is_waiting(node):
        raise BusinessProcessingError('A node that waits cannot be tested on its own: run the workflow')
    if node.get('type') == NODE_RUN_WORKFLOW:
        raise BusinessProcessingError('A node that runs a workflow cannot be tested on its own: run the workflow')
    dry_run = body.get('dry_run', False)
    if not isinstance(dry_run, bool):
        raise BusinessProcessingError('Invalid node test', data={'dry_run': ['Must be a boolean']})
    node = {key: node[key] for key in ('id', 'type', 'label', 'config') if key in node}

    step_id = body.get('step_id')
    if step_id is not None:
        if isinstance(step_id, bool) or not isinstance(step_id, int):
            raise BusinessProcessingError('Invalid node test', data={'step_id': ['An integer id is required']})
        step, source = _get_node_event(workflow, node['id'], step_id, user_id, is_admin, scope_mask)
        _check_entity_access(user_id, source.entity_type, source.entity_id, scope_mask)
        context = ai_workflows_engine_replay_context(source, ai_workflows_db_list_steps(source.id), step)
        run = ai_workflows_engine_test_node(
            workflow, node, context, trigger_type=source.trigger_type, triggered_by_user_id=user_id,
            entity_type=source.entity_type, entity_id=source.entity_id, sub_entity=source.sub_entity,
            payload=source.trigger_payload, dry_run=dry_run, source_step_id=step.id, scope_mask=scope_mask,
            run_as_user_id=run_as_user_id)
        origin = f'event of run {source.uuid} (step {step.id})'
    else:
        entity_type, entity_id = _check_entity(workflow, body.get('entity_type'), body.get('entity_id'), user_id,
                                               scope_mask, any_type=True)
        payload = _test_context_part(body, 'payload', (dict, list))
        nodes = _test_context_part(body, 'nodes', dict)
        variables = _test_context_part(body, 'vars', dict)
        context = ai_workflows_engine_test_context(TRIGGER_MANUAL, payload, entity_type, entity_id, nodes,
                                                   variables)
        run = ai_workflows_engine_test_node(
            workflow, node, context, trigger_type=TRIGGER_MANUAL, triggered_by_user_id=user_id,
            entity_type=entity_type, entity_id=entity_id, payload=payload, dry_run=dry_run, scope_mask=scope_mask,
            run_as_user_id=run_as_user_id)
        origin = f'{entity_type} #{entity_id}' if entity_type else 'a supplied context'
    mode = 'dry run' if dry_run else 'run'
    track_activity(f'AI workflow #{workflow.id} "{workflow.name}" node {node["id"]} ({node["type"]}) tested on '
                   f'{origin} as {mode} {run.uuid}', ctx_less=True)
    return ai_workflows_run_summary(run)
