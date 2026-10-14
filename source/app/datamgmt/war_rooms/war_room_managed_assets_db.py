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

"""Persistence of the registry assets attached to a war room's scope.

The listing takes the registry scope of the caller (customer membership
and row visibility, see `managed_assets_db_scope_clauses`) and the case
ids it can read: attachments linked to any other case are left out.
"""

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_scope_clauses
from app.db import db
from app.models.assets import AssetsType
from app.models.authorization import User
from app.models.cases import Cases
from app.models.customers import Client
from app.models.managed_assets import ManagedAsset
from app.models.vulnerabilities import Vulnerability
from app.models.war_rooms import WarRoomManagedAsset


def war_room_managed_assets_db_list(war_room_id, registry_scope, readable_case_ids, limit):
    """Rows `(attachment, asset, asset_type_name, customer_name, case_name,
    vulnerability, added_by_name)`, newest first, at most `limit + 1`."""
    readable_case_ids = list(readable_case_ids or ())
    case_clause = WarRoomManagedAsset.case_id.is_(None)
    if readable_case_ids:
        case_clause = or_(case_clause, WarRoomManagedAsset.case_id.in_(readable_case_ids))
    return (
        db.session.query(
            WarRoomManagedAsset,
            ManagedAsset,
            AssetsType.asset_name.label('asset_type_name'),
            Client.name.label('customer_name'),
            Cases.name.label('case_name'),
            Vulnerability,
            User.name.label('added_by_name'),
        )
        .join(ManagedAsset, ManagedAsset.managed_asset_id == WarRoomManagedAsset.managed_asset_id)
        .outerjoin(AssetsType, AssetsType.asset_id == ManagedAsset.asset_type_id)
        .outerjoin(Client, Client.client_id == ManagedAsset.client_id)
        .outerjoin(Cases, Cases.case_id == WarRoomManagedAsset.case_id)
        .outerjoin(Vulnerability, Vulnerability.vulnerability_id == WarRoomManagedAsset.vulnerability_id)
        .outerjoin(User, User.id == WarRoomManagedAsset.added_by_id)
        .filter(WarRoomManagedAsset.war_room_id == war_room_id, case_clause,
                *managed_assets_db_scope_clauses(registry_scope))
        .order_by(WarRoomManagedAsset.added_at.desc(), WarRoomManagedAsset.id.desc())
        .limit(limit + 1)
        .all()
    )


def war_room_managed_assets_db_get(war_room_id, attachment_id):
    return WarRoomManagedAsset.query.filter(
        WarRoomManagedAsset.war_room_id == war_room_id,
        WarRoomManagedAsset.id == attachment_id,
    ).first()


def war_room_managed_assets_db_find(war_room_id, managed_asset_id, case_id, vulnerability_id):
    """The attachment with exactly these links (None compared as NULL)."""
    query = WarRoomManagedAsset.query.filter(
        WarRoomManagedAsset.war_room_id == war_room_id,
        WarRoomManagedAsset.managed_asset_id == managed_asset_id,
    )
    for column, value in ((WarRoomManagedAsset.case_id, case_id),
                          (WarRoomManagedAsset.vulnerability_id, vulnerability_id)):
        query = query.filter(column.is_(None) if value is None else column == value)
    return query.first()


def war_room_managed_assets_db_count(war_room_id):
    return WarRoomManagedAsset.query.filter(WarRoomManagedAsset.war_room_id == war_room_id).count()


def war_room_managed_assets_db_add(attachment):
    db.session.add(attachment)


def war_room_managed_assets_db_delete(attachment):
    db.session.delete(attachment)


def war_room_managed_assets_db_commit() -> bool:
    """Commit; False (rolled back) on a uniqueness clash."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True
