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

"""Query helpers for the `tutorial` table."""

from sqlalchemy import or_

from app.models.tutorials import Tutorial


def list_tutorials():
    """Every tutorial, drafts included, alphabetical."""
    return Tutorial.query.order_by(Tutorial.title.asc(), Tutorial.id.asc()).all()


def list_catalogue_tutorials():
    """Published tutorials, and every version of a built-in one: the
    catalogue needs the unpublished ones too, to hide those built-ins."""
    return (
        Tutorial.query
        .filter(or_(Tutorial.published.is_(True), Tutorial.builtin_id.isnot(None)))
        .order_by(Tutorial.title.asc(), Tutorial.id.asc())
        .all()
    )


def get_tutorial(tutorial_id: int):
    return Tutorial.query.filter(Tutorial.id == tutorial_id).first()


def get_tutorial_by_builtin_id(builtin_id: str):
    return Tutorial.query.filter(Tutorial.builtin_id == builtin_id).first()
