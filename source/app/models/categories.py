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

"""Categories that file war rooms and AI workflows.

One flat list per kind, shared by every user of the kind. A war room or
a workflow is in at most one category (`category_id`, null when it is
in none); deleting a category leaves its objects uncategorised.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import String
from sqlalchemy import func

from app.db import db


CATEGORY_KIND_WAR_ROOM = 'war_room'
CATEGORY_KIND_AI_WORKFLOW = 'ai_workflow'
CATEGORY_NAME_MAX_LEN = 128


class ObjectCategory(db.Model):
    __tablename__ = 'object_category'

    id = Column(BigInteger, primary_key=True)
    kind = Column(String(32), nullable=False)
    name = Column(String(CATEGORY_NAME_MAX_LEN), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    created_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)


# A name is unique within its kind, whatever the case
Index('uq_object_category_kind_name', ObjectCategory.kind, func.lower(ObjectCategory.name), unique=True)
