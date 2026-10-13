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

from app.models.collab import CollabDoc


def collab_lock_doc(doc_name):
    """The `CollabDoc` row of `doc_name`, locked until the end of the
    transaction, or None.

    Every read-modify-write of `y_state` must go through here: two
    writers reading the same state otherwise lose one of their updates
    on commit. `populate_existing` reloads a row the session already
    holds, which would otherwise keep the state read before the lock.
    """
    return CollabDoc.query.filter_by(doc_name=doc_name).with_for_update().populate_existing().first()
