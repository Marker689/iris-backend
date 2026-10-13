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

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import db
from app.models.collab import CollabDoc


def collab_create_doc_if_missing(doc_name):
    """Insert an empty `CollabDoc` row for `doc_name` unless one exists.

    Two first opens of a document race to create its row: a plain INSERT
    fails the loser on the primary key. Here the loser waits for the
    winner's transaction and inserts nothing; both then go through
    `collab_lock_doc`. No commit.
    """
    db.session.execute(pg_insert(CollabDoc.__table__).values(doc_name=doc_name)
                       .on_conflict_do_nothing(index_elements=['doc_name']))


def collab_lock_doc(doc_name):
    """The `CollabDoc` row of `doc_name`, locked until the end of the
    transaction, or None.

    Every read-modify-write of `y_state` must go through here: two
    writers reading the same state otherwise lose one of their updates
    on commit. `populate_existing` reloads a row the session already
    holds, which would otherwise keep the state read before the lock.
    """
    return CollabDoc.query.filter_by(doc_name=doc_name).with_for_update().populate_existing().first()
