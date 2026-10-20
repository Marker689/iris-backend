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


"""Manual workflows in the object menus.

Each manual trigger of a workflow lists the object menus it shows in
(`menus` of its config: alert, case, ioc, asset…), with its own entry
next to the module and webhook entries of `on_manual_trigger_<type>`. A click starts one run
per object (one run for all of them when the trigger is a `batch`), acting as the analyst who clicked: their permissions, their
personal keys (plus the shared ones) and their daily LLM budget, so an
entry never lets an analyst do more than they could themselves. Case
objects run on their case, with the object as the trigger payload.
"""

from app import app
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_active
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_menu_batch
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_menu_triggers
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_start_from_menu
from app.iris_engine.utils.tracker import track_activity
from app.iris_engine.webhooks.dispatch import webhooks_build_event
from app.iris_engine.webhooks.dispatch import webhooks_current_actor
from app.iris_engine.webhooks.events import MANUAL_PREFIX
from app.models.ai_workflows import RUN_SKIPPED
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.errors import BusinessProcessingError

# `module_name` of the workflow entries in the object menus. Not a module:
# the invoke routes see `workflow_id` and come here instead.
AI_WORKFLOWS_MENU_MODULE_NAME = 'IRIS workflows'


def _enabled() -> bool:
    return bool(app.config.get('AI_WORKFLOWS_ENABLED', True))


def _menu_label(workflow, trigger) -> str:
    label = trigger['config'].get('menu_label')
    return (label.strip() if isinstance(label, str) else '') or workflow.name


def ai_workflow_menus_options(hook_type) -> list:
    """Menu entries of the manual triggers of the active workflows shown
    on `hook_type` objects, one per trigger. Same shape as the module
    entries, plus `workflow_id` and `workflow_trigger_id` (the trigger
    node)."""
    if not _enabled():
        return []
    return [
        {
            'manual_hook_ui_name': _menu_label(workflow, trigger),
            'hook_name': f'{MANUAL_PREFIX}{hook_type}',
            'module_name': AI_WORKFLOWS_MENU_MODULE_NAME,
            'workflow_id': workflow.id,
            'workflow_trigger_id': trigger['node_id'],
        }
        for workflow in ai_workflows_db_list_active(TRIGGER_MANUAL)
        for trigger in ai_workflows_triggers_menu_triggers(workflow, hook_type)
    ]


def _target_label(event) -> str:
    ids = event.get('object_ids') or []
    if event.get('object_id') is None and len(ids) > 1:
        return f'{event.get("object_type")} #{", #".join(str(i) for i in ids)}'
    return f'{event.get("object_type")} #{event.get("object_id")}'


def ai_workflow_menus_run(workflow_id, hook_name, targets, user_id, scope_mask=None, caseid=None,
                          trigger_node_id=None) -> tuple:
    """Start one run per target object (already loaded and access-checked
    by the route) at the trigger node `trigger_node_id`, acting as
    `user_id`. A `batch` trigger on objects of case `caseid` starts one
    run instead, the list of the objects in its payload `data`. Returns
    (started, logs), a log line per target that did not start."""
    try:
        workflow_id = int(workflow_id)
    except (TypeError, ValueError):
        raise BusinessProcessingError('Invalid workflow')
    if not isinstance(hook_name, str) or not hook_name.startswith(MANUAL_PREFIX):
        raise BusinessProcessingError('This workflow cannot be run on these objects')
    if trigger_node_id is not None and not isinstance(trigger_node_id, str):
        raise BusinessProcessingError('Invalid workflow trigger')

    actor = webhooks_current_actor()
    batch = caseid is not None and bool(targets) and ai_workflows_triggers_menu_batch(
        workflow_id, hook_name[len(MANUAL_PREFIX):], trigger_node_id)
    started = 0
    logs = []
    for target in [list(targets)] if batch else targets:
        event = webhooks_build_event(hook_name, target, caseid=caseid, actor=actor)
        label = _target_label(event)
        run, reason = ai_workflows_triggers_start_from_menu(workflow_id, event, user_id, scope_mask,
                                                            trigger_node_id=trigger_node_id)
        if run is None or run.status == RUN_SKIPPED:
            logs.append(f'{label}: {reason}')
            continue
        started += 1
        track_activity(f'AI workflow #{workflow_id} "{run.workflow_name}" started from the {label} menu '
                       f'(run {run.uuid})', caseid=caseid, ctx_less=caseid is None)
    return started, logs
