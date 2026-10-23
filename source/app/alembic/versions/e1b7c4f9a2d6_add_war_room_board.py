#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""War-room board widgets.

- `war_room.board`: the widgets of the war-room board, set by the room's
  writers (NULL = the default board).

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the column.

Revision ID: e1b7c4f9a2d6
Revises: d8a3f6c2e914
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _table_has_column


revision = 'e1b7c4f9a2d6'
down_revision = 'd8a3f6c2e914'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('war_room', 'board'):
        op.add_column('war_room', sa.Column('board', postgresql.JSONB(), nullable=True))


def downgrade():
    if _table_has_column('war_room', 'board'):
        op.drop_column('war_room', 'board')
