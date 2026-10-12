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

A manual workflow lists the object menus it shows in (`menus` of its
trigger config: alert, case, ioc, asset…), next to the module and
webhook entries of `on_manual_trigger_<type>`. A click starts one run
per object, acting as the analyst who clicked: their permissions, their
personal keys (plus the shared ones) and their daily LLM budget, so an
entry never lets an analyst do more than they could themselves. Case
objects run on their case, with the object as the trigger payload.
"""

from app import app
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_list_active
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_menu_types
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


def ai_workflow_menus_options(hook_type) -> list:
    """Menu entries of the active manual workflows shown on `hook_type`
    objects. Same shape as the module entries, plus `workflow_id`."""
    if not _enabled():
        return []
    return [
        {
            'manual_hook_ui_name': ((workflow.trigger_config or {}).get('menu_label') or '').strip()
                                   or workflow.name,
            'hook_name': f'{MANUAL_PREFIX}{hook_type}',
            'module_name': AI_WORKFLOWS_MENU_MODULE_NAME,
            'workflow_id': workflow.id,
        }
        for workflow in ai_workflows_db_list_active(TRIGGER_MANUAL)
        if hook_type in ai_workflows_triggers_menu_types(workflow)
    ]


def ai_workflow_menus_run(workflow_id, hook_name, targets, user_id, scope_mask=None, caseid=None) -> tuple:
    """Start one run per target object (already loaded and access-checked
    by the route), acting as `user_id`. Returns (started, logs), a log
    line per target that did not start."""
    try:
        workflow_id = int(workflow_id)
    except (TypeError, ValueError):
        raise BusinessProcessingError('Invalid workflow')
    if not isinstance(hook_name, str) or not hook_name.startswith(MANUAL_PREFIX):
        raise BusinessProcessingError('This workflow cannot be run on these objects')

    actor = webhooks_current_actor()
    started = 0
    logs = []
    for target in targets:
        event = webhooks_build_event(hook_name, target, caseid=caseid, actor=actor)
        label = f'{event.get("object_type")} #{event.get("object_id")}'
        run, reason = ai_workflows_triggers_start_from_menu(workflow_id, event, user_id, scope_mask)
        if run is None or run.status == RUN_SKIPPED:
            logs.append(f'{label}: {reason}')
            continue
        started += 1
        track_activity(f'AI workflow #{workflow_id} "{run.workflow_name}" started from the {label} menu '
                       f'(run {run.uuid})', caseid=caseid, ctx_less=caseid is None)
    return started, logs
