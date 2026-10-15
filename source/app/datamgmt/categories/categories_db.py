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


"""Persistence of the war-room and AI-workflow categories."""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.categories import ObjectCategory


def categories_db_list(kind):
    return (
        ObjectCategory.query
        .filter(ObjectCategory.kind == kind)
        .order_by(func.lower(ObjectCategory.name).asc(), ObjectCategory.id.asc())
        .all()
    )


def categories_db_get(kind, category_id):
    return (
        ObjectCategory.query
        .filter(ObjectCategory.kind == kind, ObjectCategory.id == category_id)
        .first()
    )


def categories_db_name_taken(kind, name, exclude_id=None):
    query = ObjectCategory.query.filter(
        ObjectCategory.kind == kind,
        func.lower(ObjectCategory.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(ObjectCategory.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def categories_db_save(category):
    """Add (when new) and commit. False, rolled back, when a concurrent
    request took the name in between."""
    db.session.add(category)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def categories_db_delete(category):
    """The foreign keys of the filed objects are ON DELETE SET NULL."""
    db.session.delete(category)
    db.session.commit()
