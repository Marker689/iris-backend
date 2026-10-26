#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Case trackers.

- `case_tracker_step`: the steps of a case (name unique per case,
  case-insensitively), any of which a tracker row can carry.
- `case_tracker`: a user-defined tab of a case, either manual or
  mirroring the case assets / IOCs / tasks / evidences, with its offered
  steps, extra columns and timeline sync.
- `case_tracker_row`: a manual row, or the stored state of a mirrored
  item, keyed by (item_type, item_id).
- `case_tracker_row_step`: the steps set on a row.
- `case_tracker_row_event`: the history of the rows.
- `case_tracker_row_timeline_event`: the case timeline event holding the
  state of a row (`state` timeline sync).

Idempotent via `_has_table`: `db.create_all()` runs before the
migrations on a fresh install and already creates the tables.

Revision ID: e7c3a9f2d4b6
Revises: b5d8e2a4c7f1
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table


revision = 'e7c3a9f2d4b6'
down_revision = 'b5d8e2a4c7f1'
branch_labels = None
depends_on = None


def _user_fk(name):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)


def _case_fk():
    return sa.Column('case_id', sa.BigInteger(), sa.ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False)


def _create_steps():
    if _has_table('case_tracker_step'):
        return
    op.create_table(
        'case_tracker_step',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        _case_fk(),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('color', sa.String(16), nullable=False, server_default=sa.text("'slate'")),
        sa.Column('icon', sa.String(64), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_case_tracker_step_case_id', 'case_tracker_step', ['case_id'])
    op.create_index('uq_case_tracker_step_name', 'case_tracker_step', ['case_id', sa.text('lower(name)')],
                    unique=True)


def _create_trackers():
    if _has_table('case_tracker'):
        return
    op.create_table(
        'case_tracker',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        _case_fk(),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('icon', sa.String(64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('step_ids', postgresql.JSONB(), nullable=True),
        sa.Column('columns', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('show_asset_flags', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('timeline_sync', postgresql.JSONB(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        _user_fk('created_by_id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("source IN ('manual', 'assets', 'iocs', 'tasks', 'evidences')",
                           name='ck_case_tracker_source'),
    )
    op.create_index('ix_case_tracker_case_id', 'case_tracker', ['case_id'])


def _create_rows():
    if _has_table('case_tracker_row'):
        return
    op.create_table(
        'case_tracker_row',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('tracker_id', sa.BigInteger(), sa.ForeignKey('case_tracker.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('item_type', sa.String(32), nullable=True),
        sa.Column('item_id', sa.BigInteger(), nullable=True),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('values', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        _user_fk('created_by_id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        _user_fk('updated_by_id'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('tracker_id', 'item_type', 'item_id', name='uq_case_tracker_row_item'),
    )
    op.create_index('ix_case_tracker_row_tracker_id', 'case_tracker_row', ['tracker_id'])


def _create_row_steps():
    if _has_table('case_tracker_row_step'):
        return
    op.create_table(
        'case_tracker_row_step',
        sa.Column('row_id', sa.BigInteger(), sa.ForeignKey('case_tracker_row.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        sa.Column('step_id', sa.BigInteger(), sa.ForeignKey('case_tracker_step.id', ondelete='CASCADE'),
                  primary_key=True, nullable=False),
        _user_fk('set_by_id'),
        sa.Column('set_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_case_tracker_row_step_step_id', 'case_tracker_row_step', ['step_id'])


def _create_row_events():
    if _has_table('case_tracker_row_event'):
        return
    op.create_table(
        'case_tracker_row_event',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('tracker_id', sa.BigInteger(), sa.ForeignKey('case_tracker.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('row_id', sa.BigInteger(), sa.ForeignKey('case_tracker_row.id', ondelete='CASCADE'),
                  nullable=False),
        _user_fk('user_id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('details', postgresql.JSONB(), nullable=True),
        sa.Column('in_timeline', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.create_index('ix_case_tracker_row_event_tracker_id', 'case_tracker_row_event', ['tracker_id'])
    op.create_index('ix_case_tracker_row_event_row_id', 'case_tracker_row_event', ['row_id'])


def _create_row_timeline_events():
    if _has_table('case_tracker_row_timeline_event'):
        return
    op.create_table(
        'case_tracker_row_timeline_event',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('row_id', sa.BigInteger(), sa.ForeignKey('case_tracker_row.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('case_event_id', sa.BigInteger(), sa.ForeignKey('cases_events.event_id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )


def upgrade():
    _create_steps()
    _create_trackers()
    _create_rows()
    _create_row_steps()
    _create_row_events()
    _create_row_timeline_events()


def downgrade():
    for table in ('case_tracker_row_timeline_event', 'case_tracker_row_event', 'case_tracker_row_step',
                  'case_tracker_row', 'case_tracker', 'case_tracker_step'):
        if _has_table(table):
            op.drop_table(table)
