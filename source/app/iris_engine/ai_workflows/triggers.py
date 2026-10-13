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

"""What starts runs: IRIS events, schedules, object menus and the
`run_workflow` node of another workflow.

The hook listener runs inside the save flow that fired the hook, so it
only builds the event envelope and hands it to Celery; the
`iris.ai_workflows.trigger` task matches workflows, extracts the entity,
applies the suppression rules and starts the runs.

Order of the checks for every trigger: chain depth, then the owner's
access to the entity (and the workflow customer scope), then the
trigger condition, then — under the workflow row lock — dedup window,
a run still active on the entity and the hourly cap. A refused access
is recorded as a minimal `skipped` run whatever the condition says, so
the run list never tells whether a condition matched an entity the
owner cannot see. The other suppressions write no run: they count on
the workflow (`skipped_count`, `last_skip_reason`, `last_skipped_at`).
"""

import datetime
import logging
import threading
import time

from flask import current_app
from flask import g
from flask import has_app_context

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_active_event_hooks
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_active_run_for_entity
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_count_runs_since
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_active
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_lock_workflow
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_open_case_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_open_war_room_ids
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_recent_dedup_run
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_rollback
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.ai_workflows.context import ai_workflows_context_eval_expression
from app.iris_engine.ai_workflows.cron import ai_workflows_cron_is_due
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_access_denial
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_insert_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_skip_run
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_customer
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_filter_accessible
from app.iris_engine.module_handler.module_handler import register_builtin_hook_listener
from app.iris_engine.webhooks.dispatch import webhooks_build_event
from app.iris_engine.webhooks.dispatch import webhooks_current_actor
from app.models.ai_workflows import ENTITY_ALERT
from app.models.ai_workflows import ENTITY_ALERT_CLUSTER
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.ai_workflows import MENU_ENTITY_TYPES
from app.models.ai_workflows import TRIGGER_CRON
from app.models.ai_workflows import TRIGGER_EVENT
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.ai_workflows import TRIGGER_WORKFLOW
from app.models.ai_workflows import ai_workflow_trigger_config
from app.models.authorization import ac_has_permission_server_administrator

logger = logging.getLogger(__name__)

_POSTLOAD_PREFIX = 'on_postload_'
# War room actions on the war room itself (others are on a sub-object)
_WAR_ROOM_OWN_ACTIONS = ('', 'create', 'update', 'delete', 'archive', 'unarchive')
_DEFAULT_MAX_TARGETS = 50
# Pages of open entities a schedule scans for ones its owner can access
_MAX_TARGET_PAGES = 5
_TARGET_PAGE = 100
# Hooks the '*' wildcard does not match: a workflow listening to
# everything would otherwise trigger on its own runs, suggestions and
# notifications
_WILDCARD_EXCLUDED = ('on_postload_ai_workflow_run_complete', 'on_postload_notification_create')
_WILDCARD_EXCLUDED_PREFIXES = ('on_postload_ai_suggestion_',)
# Seconds the hook listener reuses the active event workflows it read
_HOOKS_TTL = 10.0
_hook_cache = {}
_hook_cache_lock = threading.Lock()

_SKIP_CHAIN = 'Chain depth limit reached'
_SKIP_DUPLICATE = 'Duplicate within the dedup window'
_SKIP_ACTIVE = 'A run is still active on the entity'
_SKIP_HOURLY = 'Hourly run limit reached'
_NOT_IN_MENU = 'This workflow is not offered on these objects'
_NOT_CALLABLE = ('The workflow does not exist, is not active, has no "Called by a workflow" trigger or belongs '
                 'to another user')


def _enabled() -> bool:
    return bool(current_app.config.get('AI_WORKFLOWS_ENABLED', True))


def _wildcard_matches(hook_name) -> bool:
    return hook_name not in _WILDCARD_EXCLUDED and not hook_name.startswith(_WILDCARD_EXCLUDED_PREFIXES)


def _hooks_match(hooks, hook_name) -> bool:
    if not isinstance(hooks, list) or not isinstance(hook_name, str):
        return False
    return hook_name in hooks or ('*' in hooks and _wildcard_matches(hook_name))


def _event_hooks() -> list:
    """(workflow id, hooks) of the active event workflows, read at most
    every `_HOOKS_TTL` seconds per process. Reads in the caller's session
    without committing or rolling it back."""
    now = time.monotonic()
    with _hook_cache_lock:
        cached = _hook_cache.get('rows')
        if cached is not None and now - _hook_cache.get('at', 0.0) < _HOOKS_TTL:
            return cached
    rows = [(row[0], row[1]) for row in ai_workflows_db_active_event_hooks()]
    with _hook_cache_lock:
        _hook_cache['rows'] = rows
        _hook_cache['at'] = now
    return rows


def _chain():
    """(chain depth, run id) when the hook fires inside a run, else (None, None)."""
    if not has_app_context():
        return None, None
    return getattr(g, 'ai_workflow_chain_depth', None), getattr(g, 'ai_workflow_run_id', None)


def _on_hook(hook_name, data, caseid=None, hook_ui_name=None):
    """Built-in hook listener: never raises, never blocks on the work."""
    try:
        if not isinstance(hook_name, str) or not hook_name.startswith(_POSTLOAD_PREFIX) or not _enabled():
            return
        workflow_ids = [workflow_id for workflow_id, hooks in _event_hooks() if _hooks_match(hooks, hook_name)]
        if not workflow_ids:
            return
        event = webhooks_build_event(hook_name, data, caseid=caseid, actor=webhooks_current_actor())
        chain_depth, parent_run_id = _chain()
        from app.iris_engine.ai_workflows.tasks import ai_workflows_task_trigger
        ai_workflows_task_trigger.delay(event, workflow_ids, chain_depth, parent_run_id)
    except Exception:
        logger.exception(f'AI workflow trigger failed on hook {hook_name}')


def ai_workflows_triggers_register_listener():
    register_builtin_hook_listener(_on_hook)


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ai_workflows_triggers_entity(event) -> tuple:
    """(entity_type, entity_id, sub_entity) an event is about; entity
    None when it is about nothing a workflow can run on."""
    object_type = event.get('object_type')
    action = event.get('action') or ''
    object_id = _int(event.get('object_id'))
    data = event.get('data') if isinstance(event.get('data'), dict) else {}

    if object_type in (ENTITY_ALERT, ENTITY_ALERT_CLUSTER, ENTITY_CASE):
        return object_type, object_id, None
    if object_type == ENTITY_WAR_ROOM:
        if action in _WAR_ROOM_OWN_ACTIONS:
            return ENTITY_WAR_ROOM, object_id, None
        head = action.rsplit('_', 1)[0]
        sub_id = _int(data.get(f'{head}_id')) or _int(data.get('id'))
        return ENTITY_WAR_ROOM, object_id, {'type': f'war_room_{head}', 'id': sub_id}
    if object_type in ('ai_workflow_run', 'ai_suggestion'):
        entity_type = data.get('entity_type')
        if entity_type in ENTITY_TYPES:
            return entity_type, _int(data.get('entity_id')), {'type': object_type, 'id': _int(data.get('id'))}
        return None, None, None
    case = event.get('case') if isinstance(event.get('case'), dict) else None
    case_id = _int(case.get('id')) if case else None
    if case_id is not None:
        return ENTITY_CASE, case_id, {'type': object_type, 'id': object_id}
    return None, None, None


def _condition_context(event, entity_type, entity_id) -> dict:
    return {
        'event': event,
        'payload': event,
        'data': event.get('data'),
        'hook': event.get('event'),
        'entity_type': entity_type,
        'entity_id': entity_id,
    }


def _condition_holds(workflow, condition) -> bool:
    if condition is None:
        return True
    expression, context = condition
    try:
        return bool(ai_workflows_context_eval_expression(expression, context))
    except Exception as e:
        logger.warning(f'AI workflow #{workflow.id} trigger condition failed: {e}')
        return False


def _suppression(workflow, trigger_type, entity_type, entity_id, dedup_key, dedup_minutes, now):
    trigger_config = ai_workflow_trigger_config(workflow, trigger_type) or {}
    if dedup_key and dedup_minutes and ai_workflows_db_recent_dedup_run(
            workflow.id, dedup_key, now - datetime.timedelta(minutes=dedup_minutes)) is not None:
        return _SKIP_DUPLICATE
    if entity_type and trigger_config.get('skip_if_active', True) \
            and ai_workflows_db_active_run_for_entity(workflow.id, entity_type, entity_id) is not None:
        return _SKIP_ACTIVE
    if (workflow.max_runs_per_hour or 0) > 0 and ai_workflows_db_count_runs_since(
            workflow.id, now - datetime.timedelta(hours=1)) >= workflow.max_runs_per_hour:
        return _SKIP_HOURLY
    return None


def _guarded_start(workflow, trigger_type, *, entity_type, entity_id, sub_entity, payload, dedup_key=None,
                   dedup_minutes=0, chain_depth=0, parent_run_id=None, condition=None):
    """Start a run as the workflow owner. Returns the run, a minimal
    `skipped` run when the owner may not run on the entity, or None when
    the condition does not hold or a suppression rule applies (counted
    on the workflow). `condition` is (expression, context) or None."""
    max_depth = int(current_app.config.get('AI_WORKFLOWS_MAX_CHAIN_DEPTH', 2))
    if chain_depth > max_depth:
        ai_workflows_engine_skip_run(workflow, trigger_type, _SKIP_CHAIN)
        return None
    common = {
        'entity_type': entity_type,
        'entity_id': entity_id,
        'sub_entity': sub_entity,
        'payload': payload,
        'chain_depth': chain_depth,
        'parent_run_id': parent_run_id,
    }
    # Access first, unlocked (the check may commit), and before the
    # condition: a refused run is recorded whatever the condition says
    denial = ai_workflows_engine_access_denial(workflow, workflow.owner_id, entity_type, entity_id)
    if denial is not None:
        return ai_workflows_engine_insert_run(workflow, trigger_type, denial=denial, **common)
    if not _condition_holds(workflow, condition):
        return None

    # The workflow lock serialises concurrent triggers of the workflow
    # from the checks through the insert, so dedup and the caps hold
    workflow_id = workflow.id
    workflow = ai_workflows_db_lock_workflow(workflow_id)
    if workflow is None or not workflow.is_active:
        ai_workflows_db_commit()
        return None
    reason = _suppression(workflow, trigger_type, entity_type, entity_id, dedup_key, dedup_minutes, ai_workflows_db_utcnow())
    if reason is not None:
        # Commits, releasing the lock
        ai_workflows_engine_skip_run(workflow, trigger_type, reason)
        return None
    return ai_workflows_engine_insert_run(workflow, trigger_type, dedup_key=dedup_key, **common)


def ai_workflows_triggers_process_event(event, workflow_ids, chain_depth=None, parent_run_id=None) -> list:
    """Start the runs an event triggers. Returns the ids of the runs
    written (refused ones included; suppressed triggers write none)."""
    hook_name = event.get('event')
    entity_type, entity_id, sub_entity = ai_workflows_triggers_entity(event)
    run_ids = []
    if entity_type is None or entity_id is None:
        logger.debug(f'AI workflows: {hook_name} is not about an alert, cluster, case or war room')
        return run_ids
    depth = 0 if chain_depth is None else int(chain_depth) + 1
    for workflow_id in workflow_ids or []:
        try:
            workflow = ai_workflows_db_get(workflow_id)
            config = ai_workflow_trigger_config(workflow, TRIGGER_EVENT) if workflow is not None else None
            if config is None or not workflow.is_active or not _hooks_match(config.get('hooks'), hook_name):
                continue
            expression = config.get('condition')
            condition = (expression, _condition_context(event, entity_type, entity_id)) if expression else None
            sub_key = f':{sub_entity.get("type")}:{sub_entity.get("id")}' if sub_entity else ''
            run = _guarded_start(
                workflow, TRIGGER_EVENT,
                entity_type=entity_type, entity_id=entity_id, sub_entity=sub_entity, payload=event,
                dedup_key=f'{workflow.id}:{entity_type}:{entity_id}{sub_key}',
                dedup_minutes=_int(config.get('dedup_minutes')) or 0,
                chain_depth=depth, parent_run_id=parent_run_id, condition=condition,
            )
            if run is not None:
                run_ids.append(run.id)
        except Exception:
            logger.exception(f'AI workflow #{workflow_id} could not start on {hook_name}')
            ai_workflows_db_rollback()
    return run_ids


def _in_scope(entity_type, entity_ids, scope) -> list:
    if not scope:
        return entity_ids
    return [i for i in entity_ids if ai_workflows_entities_customer(entity_type, i) in scope]


def _accessible_targets(workflow, entity_type, fetch, limit, scope=None) -> list:
    """Up to `limit` open entities the owner can access (and in the
    workflow customer scope), paging through at most
    `_MAX_TARGET_PAGES` pages. `fetch(page_size, after_id)`; `scope`
    filters customers when `fetch` does not."""
    targets = []
    after_id = None
    for _page in range(_MAX_TARGET_PAGES):
        ids = fetch(_TARGET_PAGE, after_id)
        if not ids:
            break
        after_id = ids[-1]
        allowed = ai_workflows_entities_filter_accessible(workflow.owner_id, entity_type, ids)
        targets.extend(_in_scope(entity_type, allowed, scope))
        if len(targets) >= limit or len(ids) < _TARGET_PAGE:
            break
    return [(entity_type, i) for i in targets[:limit]]


def _cron_expression(workflow) -> str:
    return (ai_workflow_trigger_config(workflow, TRIGGER_CRON) or {}).get('cron') or ''


def _cron_targets(workflow) -> list:
    config = ai_workflow_trigger_config(workflow, TRIGGER_CRON) or {}
    target = config.get('target') or 'none'
    limit = _int(config.get('max_targets')) or _DEFAULT_MAX_TARGETS
    if target == 'war_rooms':
        return _accessible_targets(workflow, ENTITY_WAR_ROOM,
                                   lambda size, after: ai_workflows_db_open_war_room_ids(size, after_id=after), limit,
                                   scope=workflow.customer_scope or None)
    if target in ('cases', 'open_cases'):
        scope = workflow.customer_scope or None
        return _accessible_targets(
            workflow, ENTITY_CASE,
            lambda size, after: ai_workflows_db_open_case_ids(size, scope, after_id=after), limit)
    return [(None, None)]


def _claim_cron(workflow_id, now):
    """Lock the workflow and record the fire; None when not due (any
    more: another tick took it)."""
    workflow = ai_workflows_db_lock_workflow(workflow_id)
    expression = _cron_expression(workflow) if workflow is not None else ''
    if workflow is None or not workflow.is_active or not expression \
            or not ai_workflows_cron_is_due(expression, now, workflow.last_fired_at):
        ai_workflows_db_commit()
        return None
    workflow.last_fired_at = now
    ai_workflows_db_commit()
    return workflow


def ai_workflows_triggers_cron_tick(now=None) -> list:
    """Fire the due schedules. Returns the run ids."""
    now = (now or ai_workflows_db_utcnow()).replace(second=0, microsecond=0)
    run_ids = []
    for candidate in ai_workflows_db_list_active(TRIGGER_CRON):
        try:
            expression = _cron_expression(candidate)
            if not expression or not ai_workflows_cron_is_due(expression, now, candidate.last_fired_at):
                continue
            workflow = _claim_cron(candidate.id, now)
            if workflow is None:
                continue
            payload = {'cron': expression, 'fired_at': now.isoformat()}
            for entity_type, entity_id in _cron_targets(workflow):
                run = _guarded_start(workflow, TRIGGER_CRON, entity_type=entity_type, entity_id=entity_id,
                                     sub_entity=None, payload=payload)
                if run is not None:
                    run_ids.append(run.id)
        except ValueError as e:
            logger.warning(f'AI workflow #{candidate.id} has an invalid schedule: {e}')
        except Exception:
            logger.exception(f'AI workflow #{candidate.id} schedule failed')
            ai_workflows_db_rollback()
    return run_ids


# ---- Object menus -----------------------------------------------------------

def ai_workflows_triggers_menu_types(workflow) -> list:
    """Object menus (`on_manual_trigger_<type>`) an active manual workflow shows in."""
    if workflow is None or not workflow.is_active:
        return []
    menus = (ai_workflow_trigger_config(workflow, TRIGGER_MANUAL) or {}).get('menus')
    return [m for m in menus if m in MENU_ENTITY_TYPES] if isinstance(menus, list) else []


def ai_workflows_triggers_start_from_menu(workflow_id, event, user_id, scope_mask=None) -> tuple:
    """Start a run of a manual workflow from an object menu entry. It acts
    as the clicking user `user_id` (their permissions, personal keys and
    daily budget), bounded by `scope_mask`, on the entity of `event` (the
    `on_manual_trigger_*` event of the clicked object).

    Returns (run, reason): the run (a minimal `skipped` one when the user
    may not run it on the entity, `reason` then saying why), or None and
    why nothing started."""
    workflow = ai_workflows_db_get(workflow_id)
    if not _enabled() or event.get('object_type') not in ai_workflows_triggers_menu_types(workflow):
        return None, _NOT_IN_MENU
    entity_type, entity_id, sub_entity = ai_workflows_triggers_entity(event)
    if entity_type is None or entity_id is None:
        return None, _NOT_IN_MENU
    common = {
        'entity_type': entity_type,
        'entity_id': entity_id,
        'sub_entity': sub_entity,
        'payload': event,
        'triggered_by_user_id': user_id,
        'run_as_user_id': user_id,
        'scope_mask': scope_mask,
    }
    # Access first, unlocked (the check may commit)
    denial = ai_workflows_engine_access_denial(workflow, user_id, entity_type, entity_id, scope_mask)
    if denial is not None:
        return ai_workflows_engine_insert_run(workflow, TRIGGER_MANUAL, denial=denial, **common), denial

    # Under the workflow lock, like the other triggers, so the hourly cap
    # holds against concurrent clicks. No dedup nor active-run rule: the
    # analyst asked for this run.
    workflow = ai_workflows_db_lock_workflow(workflow_id)
    if not ai_workflows_triggers_menu_types(workflow):
        ai_workflows_db_commit()
        return None, _NOT_IN_MENU
    if _hourly_cap_reached(workflow):
        # Commits, releasing the lock
        ai_workflows_engine_skip_run(workflow, TRIGGER_MANUAL, _SKIP_HOURLY)
        return None, _SKIP_HOURLY
    return ai_workflows_engine_insert_run(workflow, TRIGGER_MANUAL, **common), None


# ---- Called by another workflow -------------------------------------------------

def _hourly_cap_reached(workflow) -> bool:
    return (workflow.max_runs_per_hour or 0) > 0 and ai_workflows_db_count_runs_since(
        workflow.id, ai_workflows_db_utcnow() - datetime.timedelta(hours=1)) >= workflow.max_runs_per_hour


def _callable(workflow) -> bool:
    return workflow is not None and workflow.is_active \
        and ai_workflow_trigger_config(workflow, TRIGGER_WORKFLOW) is not None


def _may_call(caller, workflow) -> bool:
    """A workflow calls only the workflows of its owner, unless that
    owner is a server administrator (who sees every workflow)."""
    if caller.owner_id is not None and caller.owner_id == workflow.owner_id:
        return True
    owner = ai_workflows_db_get_user(caller.owner_id) if caller.owner_id is not None else None
    return owner is not None and ac_has_permission_server_administrator(ac_get_effective_permissions_of_user(owner))


def ai_workflows_triggers_start_from_workflow(caller, node_id, workflow_id, inputs, wait_uuid=None) -> tuple:
    """Start a run of workflow `workflow_id` from the `run_workflow` node
    `node_id` of the run `caller`; the caller's owner must own it or be a
    server administrator. The called run is on the caller's
    entity and acts as the caller's user within its API key scope, so
    calling a workflow never gives more access than the caller has; it
    is one level deeper in the caller's chain. `trigger.payload` is
    `{inputs, caller}`; `wait_uuid` is the caller's wait the called run
    resumes when it ends (None: the caller does not wait).

    Returns (run, reason) like `ai_workflows_triggers_start_from_menu`."""
    workflow = ai_workflows_db_get(workflow_id) if workflow_id else None
    if not _enabled() or not _callable(workflow) or not _may_call(caller, workflow):
        return None, _NOT_CALLABLE
    max_depth = int(current_app.config.get('AI_WORKFLOWS_MAX_CHAIN_DEPTH', 2))
    depth = (caller.chain_depth or 0) + 1
    if depth > max_depth:
        ai_workflows_engine_skip_run(workflow, TRIGGER_WORKFLOW, _SKIP_CHAIN)
        return None, f'{_SKIP_CHAIN} (AI_WORKFLOWS_MAX_CHAIN_DEPTH is {max_depth})'
    payload = {
        'inputs': inputs or {},
        'caller': {
            'run_uuid': str(caller.uuid) if caller.uuid else None,
            'workflow_id': caller.workflow_id,
            'workflow_name': caller.workflow_name,
            'node_id': node_id,
        },
    }
    common = {
        'entity_type': caller.entity_type,
        'entity_id': caller.entity_id,
        'sub_entity': caller.sub_entity,
        'payload': payload,
        'triggered_by_user_id': caller.triggered_by_user_id,
        'run_as_user_id': caller.run_as_user_id,
        'scope_mask': caller.scope_mask,
        'chain_depth': depth,
        'parent_run_id': caller.id,
    }
    # Access first, unlocked (the check may commit)
    denial = ai_workflows_engine_access_denial(workflow, caller.run_as_user_id, caller.entity_type,
                                               caller.entity_id, caller.scope_mask)
    if denial is not None:
        return ai_workflows_engine_insert_run(workflow, TRIGGER_WORKFLOW, denial=denial, **common), denial

    # Hourly cap under the workflow lock; no dedup nor active-run rule:
    # the calling workflow asked for this run
    workflow = ai_workflows_db_lock_workflow(workflow_id)
    if not _callable(workflow):
        ai_workflows_db_commit()
        return None, _NOT_CALLABLE
    if _hourly_cap_reached(workflow):
        # Commits, releasing the lock
        ai_workflows_engine_skip_run(workflow, TRIGGER_WORKFLOW, _SKIP_HOURLY)
        return None, _SKIP_HOURLY
    return ai_workflows_engine_insert_run(workflow, TRIGGER_WORKFLOW, caller_wait_uuid=wait_uuid, **common), None
