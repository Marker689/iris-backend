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

from app.models.tutorials import Tutorial


def list_tutorials():
    """Every tutorial, drafts included, alphabetical."""
    return Tutorial.query.order_by(Tutorial.title.asc(), Tutorial.id.asc()).all()


def list_published_tutorials():
    return (
        Tutorial.query
        .filter(Tutorial.published.is_(True))
        .order_by(Tutorial.title.asc(), Tutorial.id.asc())
        .all()
    )


def get_tutorial(tutorial_id: int):
    return Tutorial.query.filter(Tutorial.id == tutorial_id).first()
