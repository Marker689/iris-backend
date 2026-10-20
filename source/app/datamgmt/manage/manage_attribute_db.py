#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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

import copy
import json
import logging as logger
from sqlalchemy.orm.attributes import flag_modified

from app.db import db
from app import app
from app.models.assets import CaseAssets
from app.models.evidences import CaseReceivedFile
from app.models.models import CaseTasks
from app.models.cases import Cases
from app.models.cases import CasesEvent
from app.models.customers import Client
from app.models.models import CustomAttribute
from app.models.iocs import Ioc
from app.models.managed_assets import ManagedAsset
from app.models.models import Notes

log = logger.getLogger(__name__)


def update_all_attributes(object_type, previous_attribute, partial_overwrite=False, complete_overwrite=False):

    obj_list = []
    if object_type == 'ioc':
        obj_list = Ioc.query.all()
    elif object_type == 'event':
        obj_list = CasesEvent.query.all()
    elif object_type == 'asset':
        obj_list = CaseAssets.query.all()
    elif object_type == 'task':
        obj_list = CaseTasks.query.all()
    elif object_type == 'note':
        obj_list = Notes.query.all()
    elif object_type == 'evidence':
        obj_list = CaseReceivedFile.query.all()
    elif object_type == 'case':
        obj_list = Cases.query.all()
    elif object_type == 'client':
        obj_list = Client.query.all()
    elif object_type == 'managed_asset':
        obj_list = ManagedAsset.query.all()

    target_attr = get_default_custom_attributes(object_type)

    app.logger.info(f'Migrating {len(obj_list)} objects of type {object_type}')
    for obj in obj_list:

        if complete_overwrite or obj.custom_attributes is None:
            app.logger.info('Achieving complete overwrite')
            obj.custom_attributes = target_attr
            flag_modified(obj, "custom_attributes")
            db.session.commit()
            continue

        for tab in target_attr:

            if obj.custom_attributes.get(tab) is None or partial_overwrite:
                app.logger.info(f'Migrating {tab}')
                flag_modified(obj, "custom_attributes")
                obj.custom_attributes[tab] = target_attr[tab]

            else:
                for element in target_attr[tab]:
                    if element not in obj.custom_attributes[tab]:
                        app.logger.info(f'Migrating {element}')
                        flag_modified(obj, "custom_attributes")
                        obj.custom_attributes[tab][element] = target_attr[tab][element]

                    else:
                        if obj.custom_attributes[tab][element]['type'] != target_attr[tab][element]['type']:
                            if (obj.custom_attributes[tab][element]['value'] == target_attr[tab][element]['value']) or \
                                (obj.custom_attributes[tab][element]['type'] in ('input_string', 'input_text_field') and
                                 target_attr[tab][element]['type'] in ('input_string', 'input_text_field')):
                                flag_modified(obj, "custom_attributes")
                                obj.custom_attributes[tab][element]['type'] = target_attr[tab][element]['type']

                        if 'mandatory' in target_attr[tab][element] \
                                and obj.custom_attributes[tab][element]['mandatory'] != target_attr[tab][element]['mandatory']:
                            flag_modified(obj, "custom_attributes")
                            obj.custom_attributes[tab][element]['mandatory'] = target_attr[tab][element]['mandatory']

        if partial_overwrite:
            for tab in previous_attribute:
                if not target_attr.get(tab):
                    if obj.custom_attributes.get(tab):
                        flag_modified(obj, "custom_attributes")
                        obj.custom_attributes.pop(tab)

                for element in previous_attribute[tab]:
                    if target_attr.get(tab):
                        if not target_attr[tab].get(element):
                            if obj.custom_attributes[tab].get(element):
                                flag_modified(obj, "custom_attributes")
                                obj.custom_attributes[tab].pop(element)

        # Commit will only be effective if we flagged a modification, reducing load on the DB
        db.session.commit()


def get_default_custom_attributes(object_type):
    ca = CustomAttribute.query.filter(CustomAttribute.attribute_for == object_type).first()
    return ca.attribute_content


def add_tab_attribute(obj, tab_name):
    """
    Add a new custom tab to an object ID
    """
    if not obj:
        return False

    attribute = obj.custom_attributes
    if tab_name in attribute:
        return True

    attribute[tab_name] = {}
    flag_modified(obj, "custom_attributes")
    db.session.commit()

    return True


def add_tab_attribute_field(obj, tab_name, field_name, field_type, field_value, mandatory=None, field_options=None):
    if not obj:
        return False

    attribute = obj.custom_attributes
    if attribute is None:
        attribute = {}

    if tab_name not in attribute:
        attribute[tab_name] = {}

    attr = {
        "mandatory": mandatory if mandatory is not None else False,
        "type": field_type,
        "value": field_value
    }
    if field_options:
        attr['options'] = field_options

    attribute[tab_name][field_name] = attr

    obj.custom_attributes = attribute

    flag_modified(obj, "custom_attributes")
    db.session.commit()

    return True


def _is_stored_attribute(value):
    """Whether `value` has the {type, value, mandatory} shape objects persist."""
    return isinstance(value, dict) and 'type' in value


def _submitted_value(value):
    """The bare value of a submitted attribute.

    Clients are expected to send bare values, but a client that echoes back
    what the API returned sends the stored {type, value, mandatory} shape;
    storing that as the value would nest it one level deeper on every save.
    """
    if _is_stored_attribute(value) and 'value' in value:
        return value['value']
    return value


def _schema_defaults(object_type):
    """A private copy of the admin schema for `object_type` ({} when there is none).

    The schema row's JSON is shared with the session; handing it out as-is
    lets callers mutate the schema through the object they attach it to.
    """
    row = CustomAttribute.query.filter(CustomAttribute.attribute_for == object_type).first()
    content = row.attribute_content if row is not None else None
    return copy.deepcopy(content) if isinstance(content, dict) else {}


def normalize_object_attributes(stored, defaults):
    """Return a copy of an object's custom attributes in the canonical shape.

    * `None` / non-dict columns become `{}` (objects created without attributes).
    * Every schema field is present; missing ones take the schema definition.
    * Bare legacy values ({"tab": {"field": "x"}}, as written by the v2 case
      update before the merge was applied) are rebuilt from the schema
      definition, keeping the value; outside the schema they become strings.
    * Well-formed entries, including module-written tabs, are kept as they are.
    """
    result = copy.deepcopy(stored) if isinstance(stored, dict) else {}

    for tab, fields in (defaults or {}).items():
        if not isinstance(fields, dict):
            continue
        stored_tab = result.get(tab)
        if not isinstance(stored_tab, dict):
            stored_tab = result[tab] = {}
        for field, definition in fields.items():
            if not _is_stored_attribute(definition):
                continue
            current = stored_tab.get(field)
            if _is_stored_attribute(current):
                current.setdefault('value', None)
                continue
            rebuilt = copy.deepcopy(definition)
            if current is not None:
                rebuilt['value'] = current
            stored_tab[field] = rebuilt

    for tab, fields in list(result.items()):
        if not isinstance(fields, dict):
            del result[tab]
            continue
        for field, current in fields.items():
            if _is_stored_attribute(current):
                current.setdefault('value', None)
            else:
                fields[field] = {'type': 'input_string', 'mandatory': False, 'value': current}

    return result


def merge_custom_attributes(data, obj_id, object_type, overwrite=False):
    """Merge submitted attribute values into an object's custom attributes.

    `data` maps tab -> field -> bare value. Only values change: field types,
    `html` fields (module or admin content) and tabs the client did not send
    are preserved. Returns the object's resulting attributes, or the schema
    defaults filled with `data` when no object is given (creation).
    """
    obj = None
    if obj_id:
        if object_type == 'ioc':
            obj = Ioc.query.filter(Ioc.ioc_id == obj_id).first()
        elif object_type == 'event':
            obj = CasesEvent.query.filter(CasesEvent.event_id == obj_id).first()
        elif object_type == 'asset':
            obj = CaseAssets.query.filter(CaseAssets.asset_id == obj_id).first()
        elif object_type == 'task':
            obj = CaseTasks.query.filter(CaseTasks.id == obj_id).first()
        elif object_type == 'note':
            obj = Notes.query.filter(Notes.note_id == obj_id).first()
        elif object_type == 'evidence':
            obj = CaseReceivedFile.query.filter(CaseReceivedFile.id == obj_id).first()
        elif object_type == 'case':
            obj = Cases.query.filter(Cases.case_id == obj_id).first()
        elif object_type == 'client':
            obj = Client.query.filter(Client.client_id == obj_id).first()
        elif object_type == 'managed_asset':
            obj = ManagedAsset.query.filter(ManagedAsset.managed_asset_id == obj_id).first()

        if not obj:
            return data

        if overwrite:
            log.warning(f'Overwriting all {object_type}')
            return get_default_custom_attributes(object_type)

        # Fill gaps from the schema instead of skipping (values silently lost) or
        # crashing (NULL column, bare legacy values) on objects that predate it.
        merged = normalize_object_attributes(obj.custom_attributes, _schema_defaults(object_type))
        _apply_submitted_values(merged, data, object_type)

        if merged != obj.custom_attributes:
            obj.custom_attributes = merged
            flag_modified(obj, "custom_attributes")

        # Commit will only be effective if we flagged a modification, reducing load on the DB
        db.session.commit()
        return obj.custom_attributes

    default_attr = normalize_object_attributes({}, _schema_defaults(object_type))
    _apply_submitted_values(default_attr, data, object_type)
    return default_attr


def _apply_submitted_values(attributes, data, object_type):
    """Write the submitted bare values into normalized `attributes`, in place."""
    if not isinstance(data, dict):
        return
    for tab, fields in data.items():
        if attributes.get(tab) is None:
            log.warning(f'Ignoring custom attribute tab {tab!r}: unknown for this {object_type}')
            continue
        if not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            stored = attributes[tab].get(field)
            if stored is None:
                log.warning(f'Ignoring custom attribute field {tab!r}/{field!r}: unknown for this {object_type}')
                continue
            if stored.get('type') == 'html':
                # html content comes from the schema or a module, never from a client
                continue
            stored['value'] = _submitted_value(value)


def validate_attribute(attribute):
    logs = []
    try:
        data = json.loads(attribute)
    except Exception as e:
        return None, [str(e)]

    for tab in data:
        for field in data[tab]:
            if not data[tab][field].get('type'):
                logs.append(f'{tab}::{field} is missing mandatory "type" tag')
                continue

            field_type = data[tab][field].get('type')
            if field_type in ['input_string', 'input_textfield', 'input_checkbox', 'input_select',
                              'input_date', 'input_datetime']:
                if data[tab][field].get('mandatory') is None:
                    logs.append(f'{tab} -> {field} of type {field_type} is missing mandatory "mandatory" tag')

                elif not isinstance(data[tab][field].get('mandatory'), bool):
                    logs.append(f'{tab} -> {field} -> "mandatory" expects a value of type bool, '
                                f'but got {type(data[tab][field].get("mandatory"))}')

                if data[tab][field].get('value') is None:
                    logs.append(f'{tab} -> {field} of type {field_type} is missing mandatory "value" tag')

                if field_type == 'input_checkbox' and not isinstance(data[tab][field].get('value'), bool):
                    logs.append(f'{tab} -> {field} of type {field_type} expects a value of type bool, '
                                f'but got {type(data[tab][field]["value"])}')

                if field_type in ['input_string', 'input_textfield', 'input_date', 'input_datetime']:
                    if not isinstance(data[tab][field].get('value'), str):
                        logs.append(f'{tab} -> {field} of type {field_type} expects a value of type str, '
                                    f'but got {type(data[tab][field]["value"])}')

                if field_type == 'input_select':
                    if data[tab][field].get('options') is None:
                        logs.append(f'{tab} -> {field} of type {field_type} is missing mandatory "options" tag')
                        continue

                    if not isinstance(data[tab][field].get('options'), list):
                        logs.append(f'{tab} -> {field} of type {field_type} expects a value of type list, '
                                    f'but got {type(data[tab][field]["value"])}')

                    for opt in data[tab][field].get('options'):
                        if not isinstance(opt, str):
                            logs.append(f'{tab} -> {field} -> "options" expects a list of str, '
                                        f'but got {type(opt)}')

            elif field_type in ['raw', 'html']:
                if data[tab][field].get('value') is None:
                    logs.append(f'{tab} -> {field} of type {field_type} is missing mandatory "value" tag')

            else:
                logs.append(f'{tab} -> {field}, unknown field type "{field_type}"')

    return data, logs
