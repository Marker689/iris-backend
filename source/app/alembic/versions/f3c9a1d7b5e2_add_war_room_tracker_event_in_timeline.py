#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""War-room tracker history: which changes are in the timelines.

- `war_room_tracker_row_event.in_timeline`: the change was written by a
  `changes` timeline sync (live or by the backfill). The changes made
  before the sync was turned on stay false and can be written later.

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the column.

Revision ID: f3c9a1d7b5e2
Revises: e1b7c4f9a2d6
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'f3c9a1d7b5e2'
down_revision = 'e1b7c4f9a2d6'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('war_room_tracker_row_event', 'in_timeline'):
        op.add_column('war_room_tracker_row_event',
                      sa.Column('in_timeline', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade():
    if _table_has_column('war_room_tracker_row_event', 'in_timeline'):
        op.drop_column('war_room_tracker_row_event', 'in_timeline')
