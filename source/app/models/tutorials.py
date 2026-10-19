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

"""Admin-authored guided tutorials.

The SPA ships a catalogue of built-in tutorials; server administrators can
add their own from Settings → Tutorials (the team's own procedures, the
house way of opening a case…). A published row shows up in every user's
Help → Tutorials catalogue next to the built-in ones and runs in the same
overlay. The built-in tutorials can be edited the same way: the edited
version is a row pointing at the built-in through `builtin_id`.

`steps` is the step list exactly as the SPA's tutorial engine reads it,
stored as JSONB: the engine owns the format, the backend only validates
its shape and bounds (`TutorialSchema`) so a bad row cannot break the
catalogue for everyone.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db import db


class Tutorial(db.Model):
    __tablename__ = 'tutorial'

    id = Column(BigInteger, primary_key=True)
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=False, default='')
    # Free text shown in the catalogue, e.g. `~5 min`.
    duration = Column(String(32), nullable=False, default='')
    steps = Column(JSONB, nullable=False, default=list)
    # Drafts are only listed to administrators.
    published = Column(Boolean, nullable=False, default=False)
    # Set when the row is an administrator's version of a built-in
    # tutorial (its SPA id, e.g. `first-investigation`): published, it
    # replaces the built-in in the catalogue; unpublished, it hides it.
    builtin_id = Column(String(64), nullable=True, unique=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False,
                        server_default=func.now(), onupdate=func.now())
    created_by = Column(ForeignKey('user.id'), nullable=True)
