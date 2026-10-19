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

"""Business layer for admin-authored guided tutorials.

Tutorials are org-wide, so every write is tracked `ctx_less`, like the
banners and the server settings.
"""

from app.datamgmt.db_operations import db_create
from app.datamgmt.db_operations import db_delete
from app.datamgmt.manage.manage_tutorials_db import get_tutorial
from app.datamgmt.manage.manage_tutorials_db import list_published_tutorials
from app.datamgmt.manage.manage_tutorials_db import list_tutorials
from app.db import db
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import ObjectNotFoundError
from app.models.tutorials import Tutorial


def tutorials_list() -> list[Tutorial]:
    return list_tutorials()


def tutorials_list_published() -> list[Tutorial]:
    return list_published_tutorials()


def tutorials_get(identifier: int) -> Tutorial:
    tutorial = get_tutorial(identifier)
    if not tutorial:
        raise ObjectNotFoundError()
    return tutorial


def tutorials_create(tutorial: Tutorial, user) -> Tutorial:
    tutorial.created_by = user.id
    db_create(tutorial)
    track_activity(f'Tutorial #{tutorial.id} "{tutorial.title}" created', ctx_less=True)
    return tutorial


def tutorials_update(tutorial: Tutorial) -> Tutorial:
    db.session.commit()
    track_activity(f'Tutorial #{tutorial.id} "{tutorial.title}" updated', ctx_less=True)
    return tutorial


def tutorials_delete(tutorial: Tutorial) -> None:
    tutorial_id = tutorial.id
    title = tutorial.title
    db_delete(tutorial)
    track_activity(f'Tutorial #{tutorial_id} "{title}" deleted', ctx_less=True)
