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

"""Registry assets (Assets Manager) attached to a war room's scope.

An attachment puts an existing registry asset in the scope of a war room
without creating anything in a case. It can be linked to one of the
attached cases, to a catalogue vulnerability, to both or to neither.

The blueprint hands this module the caller's registry scope, the
attached cases it can read and whether it can read vulnerabilities:

- assets outside the registry scope are never listed and cannot be
  attached (reported as `not_found`, like the registry itself does);
- attachments linked to a case the caller cannot read are left out, and
  only readable attached cases can be linked;
- without the vulnerability permission, the vulnerability link and the
  registry findings are dropped from the rows and cannot be set.
"""

from app.business.managed_assets import managed_assets_get
from app.business.vulnerabilities import vulnerabilities_isoformat
from app.business.vulnerabilities import vulnerabilities_parse_id
from app.business.vulnerabilities import vulnerabilities_parse_text
from app.business.vulnerabilities import vulnerabilities_serialize_short
from app.business.vulnerability_findings import vulnerability_findings_managed_tags
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_get
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_add
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_commit
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_count
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_delete
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_find
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_get
from app.datamgmt.war_rooms.war_room_managed_assets_db import war_room_managed_assets_db_list
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WarRoomManagedAsset


WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT = 2000
WAR_ROOM_MANAGED_ASSETS_MAX_PER_ROOM = 5000
WAR_ROOM_MANAGED_ASSETS_MAX_BATCH = 200
_NOTE_MAX_LENGTH = 2000


def _serialize(row, include_vulnerabilities, tags):
    attachment = row.WarRoomManagedAsset
    asset = row.ManagedAsset
    item = {
        'id': attachment.id,
        'managed_asset_id': asset.managed_asset_id,
        'asset_name': asset.name,
        'asset_type_id': asset.asset_type_id,
        'asset_type_name': row.asset_type_name,
        'asset_ip': asset.ip,
        'asset_domain': asset.domain,
        'asset_description': asset.description,
        'asset_tags': asset.tags,
        'criticality': asset.criticality,
        'environment': asset.environment,
        'owner': asset.owner,
        'is_active': asset.is_active,
        'customer_id': asset.client_id,
        'customer_name': row.customer_name,
        'case_id': attachment.case_id,
        'case_name': row.case_name,
        'note': attachment.note,
        'added_at': vulnerabilities_isoformat(attachment.added_at),
        'added_by_id': attachment.added_by_id,
        'added_by_name': row.added_by_name,
    }
    if include_vulnerabilities:
        vulnerability = row.Vulnerability
        item['vulnerability'] = vulnerabilities_serialize_short(vulnerability) if vulnerability else None
        item['vulnerabilities'] = tags.get(asset.managed_asset_id, [])
    return item


def war_room_managed_assets_list(war_room_id, registry_scope, readable_case_ids, include_vulnerabilities):
    """The attachments the caller can see, newest first (capped)."""
    rows = war_room_managed_assets_db_list(war_room_id, registry_scope, readable_case_ids,
                                           WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT)
    truncated = len(rows) > WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT
    rows = rows[:WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT]
    tags = {}
    if include_vulnerabilities:
        tags = vulnerability_findings_managed_tags({row.ManagedAsset.managed_asset_id for row in rows})
    return {
        'data': [_serialize(row, include_vulnerabilities, tags) for row in rows],
        'truncated': truncated,
        'limit': WAR_ROOM_MANAGED_ASSETS_LIST_LIMIT,
    }


def _parse_case_id(body, readable_case_ids):
    case_id = vulnerabilities_parse_id(body.get('case_id'), 'case_id')
    if case_id is not None and case_id not in readable_case_ids:
        raise BusinessProcessingError('Case not attached to this war room or access denied')
    return case_id


def _parse_vulnerability_id(body, can_link_vulnerability):
    vulnerability_id = vulnerabilities_parse_id(body.get('vulnerability_id'), 'vulnerability_id')
    if vulnerability_id is None:
        return None
    if not can_link_vulnerability:
        raise BusinessProcessingError('Linking a vulnerability needs the vulnerabilities_read permission')
    if vulnerabilities_db_get(vulnerability_id) is None:
        raise BusinessProcessingError('Unknown vulnerability_id')
    return vulnerability_id


def _parse_managed_asset_ids(value):
    if not isinstance(value, list) or not value:
        raise BusinessProcessingError('managed_asset_ids must be a non-empty list')
    if len(value) > WAR_ROOM_MANAGED_ASSETS_MAX_BATCH:
        raise BusinessProcessingError(f'At most {WAR_ROOM_MANAGED_ASSETS_MAX_BATCH} assets at once')
    ids = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise BusinessProcessingError('managed_asset_ids must be integers')
        if item not in ids:
            ids.append(item)
    return ids


def _is_visible(managed_asset_id, registry_scope):
    try:
        managed_assets_get(managed_asset_id, registry_scope)
    except ObjectNotFoundError:
        return False
    return True


def war_room_managed_assets_attach(war_room_id, body, user_id, registry_scope, readable_case_ids,
                                   can_link_vulnerability):
    """Attach registry assets with the same optional links. Idempotent per
    asset: `{created, existing, not_found}` lists of managed asset ids."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    managed_asset_ids = _parse_managed_asset_ids(body.get('managed_asset_ids'))
    case_id = _parse_case_id(body, readable_case_ids)
    vulnerability_id = _parse_vulnerability_id(body, can_link_vulnerability)
    note = vulnerabilities_parse_text(body.get('note'), 'note', _NOTE_MAX_LENGTH)

    result = {'created': [], 'existing': [], 'not_found': []}
    pending = []
    for managed_asset_id in managed_asset_ids:
        if not _is_visible(managed_asset_id, registry_scope):
            result['not_found'].append(managed_asset_id)
        elif war_room_managed_assets_db_find(war_room_id, managed_asset_id, case_id, vulnerability_id) is not None:
            result['existing'].append(managed_asset_id)
        else:
            pending.append(managed_asset_id)
    if not pending:
        return result
    if war_room_managed_assets_db_count(war_room_id) + len(pending) > WAR_ROOM_MANAGED_ASSETS_MAX_PER_ROOM:
        raise BusinessProcessingError(
            f'A war room holds at most {WAR_ROOM_MANAGED_ASSETS_MAX_PER_ROOM} attached registry assets')

    for managed_asset_id in pending:
        war_room_managed_assets_db_add(WarRoomManagedAsset(
            war_room_id=war_room_id, managed_asset_id=managed_asset_id, case_id=case_id,
            vulnerability_id=vulnerability_id, note=note, added_by_id=user_id,
        ))
    if not war_room_managed_assets_db_commit():
        # Attached concurrently with the same links.
        raise BusinessProcessingError('Some of these assets were attached meanwhile, reload and retry')
    result['created'] = pending
    track_activity(f'attached {len(pending)} registry asset(s) to the scope', war_room_id=war_room_id)
    return result


def _get_visible(war_room_id, attachment_id, registry_scope, readable_case_ids):
    """The attachment, or ObjectNotFoundError when the caller cannot see it."""
    attachment = war_room_managed_assets_db_get(war_room_id, attachment_id)
    if attachment is None:
        raise ObjectNotFoundError()
    if attachment.case_id is not None and attachment.case_id not in readable_case_ids:
        raise ObjectNotFoundError()
    if not _is_visible(attachment.managed_asset_id, registry_scope):
        raise ObjectNotFoundError()
    return attachment


def war_room_managed_assets_update(war_room_id, attachment_id, body, registry_scope, readable_case_ids,
                                   can_link_vulnerability) -> None:
    """Change the case link, the vulnerability link or the note. Absent
    keys are left untouched; null clears a link."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    attachment = _get_visible(war_room_id, attachment_id, registry_scope, readable_case_ids)
    if 'case_id' in body:
        attachment.case_id = _parse_case_id(body, readable_case_ids)
    if 'vulnerability_id' in body:
        if not can_link_vulnerability:
            raise BusinessProcessingError('Linking a vulnerability needs the vulnerabilities_read permission')
        attachment.vulnerability_id = _parse_vulnerability_id(body, can_link_vulnerability)
    if 'note' in body:
        attachment.note = vulnerabilities_parse_text(body.get('note'), 'note', _NOTE_MAX_LENGTH)
    if not war_room_managed_assets_db_commit():
        raise BusinessProcessingError('This asset is already attached with these links')


def war_room_managed_assets_detach(war_room_id, attachment_id, registry_scope, readable_case_ids) -> None:
    attachment = _get_visible(war_room_id, attachment_id, registry_scope, readable_case_ids)
    managed_asset_id = attachment.managed_asset_id
    war_room_managed_assets_db_delete(attachment)
    war_room_managed_assets_db_commit()
    track_activity(f'detached registry asset #{managed_asset_id} from the scope', war_room_id=war_room_id)
