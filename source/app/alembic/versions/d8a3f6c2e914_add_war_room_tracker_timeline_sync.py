#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""War-room tracker timeline sync.

- `war_room_tracker.timeline_sync`: what the tracker sends to the
  timelines (NULL = nothing).
- `war_room_timeline_event.tracker_id` / `visibility_case_ids` /
  `visibility_managed_asset_id`: the tracker that wrote the event and who
  in the room may read it.
- `war_room_tracker_row_timeline_event`: the event holding the state of a
  row, per case timeline or for the war-room timeline.

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the table and the columns.

Revision ID: d8a3f6c2e914
Revises: c4d9e2a7b1f3
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'd8a3f6c2e914'
down_revision = 'c4d9e2a7b1f3'
branch_labels = None
depends_on = None


def _add_column(table, column):
    if not _table_has_column(table, column.name):
        op.add_column(table, column)


def upgrade():
    _add_column('war_room_tracker', sa.Column('timeline_sync', postgresql.JSONB(), nullable=True))
    _add_column('war_room_timeline_event',
                sa.Column('tracker_id', sa.BigInteger(),
                          sa.ForeignKey('war_room_tracker.id', ondelete='SET NULL'), nullable=True))
    _add_column('war_room_timeline_event', sa.Column('visibility_case_ids', postgresql.JSONB(), nullable=True))
    _add_column('war_room_timeline_event', sa.Column('visibility_managed_asset_id', sa.BigInteger(), nullable=True))

    if not _has_table('war_room_tracker_row_timeline_event'):
        op.create_table(
            'war_room_tracker_row_timeline_event',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('row_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_tracker_row.id', ondelete='CASCADE'), nullable=False),
            sa.Column('case_id', sa.BigInteger(), sa.ForeignKey('cases.case_id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('case_event_id', sa.BigInteger(),
                      sa.ForeignKey('cases_events.event_id', ondelete='SET NULL'), nullable=True),
            sa.Column('war_room_event_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_timeline_event.id', ondelete='SET NULL'), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        )
        op.create_index('ix_war_room_tracker_row_timeline_event_row_id', 'war_room_tracker_row_timeline_event',
                        ['row_id'])


def downgrade():
    if _has_table('war_room_tracker_row_timeline_event'):
        op.drop_table('war_room_tracker_row_timeline_event')
    for column in ('visibility_managed_asset_id', 'visibility_case_ids', 'tracker_id'):
        if _table_has_column('war_room_timeline_event', column):
            op.drop_column('war_room_timeline_event', column)
    if _table_has_column('war_room_tracker', 'timeline_sync'):
        op.drop_column('war_room_tracker', 'timeline_sync')
