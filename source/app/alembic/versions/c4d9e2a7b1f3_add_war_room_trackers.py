#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""War-room trackers.

- `war_room_tracker_step`: the steps of a war room (name unique per room,
  case-insensitively), any of which a tracker row can carry.
- `war_room_tracker`: a user-defined tab of the war-room scope, either
  manual or mirroring the room's assets / IOCs / customers / cases /
  registry assets, with its offered steps and extra columns.
- `war_room_tracker_row`: a manual row, or the stored state of a
  mirrored item, keyed by (item_type, item_id).
- `war_room_tracker_row_step`: the steps set on a row.
- `war_room_tracker_row_event`: the history of the rows.
- `tracker_template`: global bundles of steps and tracker definitions.

Idempotent via `_has_table`: `db.create_all()` runs before the
migrations on a fresh install and already creates the tables.

Revision ID: c4d9e2a7b1f3
Revises: b7e2d4a9c1f6
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table


revision = 'c4d9e2a7b1f3'
down_revision = 'b7e2d4a9c1f6'
branch_labels = None
depends_on = None


def _user_fk(name):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)


def _create_steps():
    if _has_table('war_room_tracker_step'):
        return
    op.create_table(
        'war_room_tracker_step',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('war_room_id', sa.BigInteger(),
                  sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('color', sa.String(16), nullable=False, server_default=sa.text("'slate'")),
        sa.Column('icon', sa.String(64), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_war_room_tracker_step_war_room_id', 'war_room_tracker_step', ['war_room_id'])
    op.create_index('uq_war_room_tracker_step_name', 'war_room_tracker_step',
                    ['war_room_id', sa.text('lower(name)')], unique=True)


def _create_trackers():
    if _has_table('war_room_tracker'):
        return
    op.create_table(
        'war_room_tracker',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('war_room_id', sa.BigInteger(),
                  sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('icon', sa.String(64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('step_ids', postgresql.JSONB(), nullable=True),
        sa.Column('columns', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('show_asset_flags', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        _user_fk('created_by_id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("source IN ('manual', 'assets', 'iocs', 'customers', 'cases', 'registry_assets')",
                           name='ck_war_room_tracker_source'),
    )
    op.create_index('ix_war_room_tracker_war_room_id', 'war_room_tracker', ['war_room_id'])


def _create_rows():
    if _has_table('war_room_tracker_row'):
        return
    op.create_table(
        'war_room_tracker_row',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('tracker_id', sa.BigInteger(),
                  sa.ForeignKey('war_room_tracker.id', ondelete='CASCADE'), nullable=False),
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
        sa.UniqueConstraint('tracker_id', 'item_type', 'item_id', name='uq_war_room_tracker_row_item'),
    )
    op.create_index('ix_war_room_tracker_row_tracker_id', 'war_room_tracker_row', ['tracker_id'])


def _create_row_steps():
    if _has_table('war_room_tracker_row_step'):
        return
    op.create_table(
        'war_room_tracker_row_step',
        sa.Column('row_id', sa.BigInteger(),
                  sa.ForeignKey('war_room_tracker_row.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('step_id', sa.BigInteger(),
                  sa.ForeignKey('war_room_tracker_step.id', ondelete='CASCADE'), primary_key=True,
                  nullable=False),
        _user_fk('set_by_id'),
        sa.Column('set_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_war_room_tracker_row_step_step_id', 'war_room_tracker_row_step', ['step_id'])


def _create_row_events():
    if _has_table('war_room_tracker_row_event'):
        return
    op.create_table(
        'war_room_tracker_row_event',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('tracker_id', sa.BigInteger(),
                  sa.ForeignKey('war_room_tracker.id', ondelete='CASCADE'), nullable=False),
        sa.Column('row_id', sa.BigInteger(),
                  sa.ForeignKey('war_room_tracker_row.id', ondelete='CASCADE'), nullable=False),
        _user_fk('user_id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('details', postgresql.JSONB(), nullable=True),
    )
    op.create_index('ix_war_room_tracker_row_event_tracker_id', 'war_room_tracker_row_event', ['tracker_id'])
    op.create_index('ix_war_room_tracker_row_event_row_id', 'war_room_tracker_row_event', ['row_id'])


def _create_templates():
    if _has_table('tracker_template'):
        return
    op.create_table(
        'tracker_template',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False, unique=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('steps', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('trackers', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        _user_fk('created_by_id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )


def upgrade():
    _create_steps()
    _create_trackers()
    _create_rows()
    _create_row_steps()
    _create_row_events()
    _create_templates()


def downgrade():
    for table in ('tracker_template', 'war_room_tracker_row_event', 'war_room_tracker_row_step',
                  'war_room_tracker_row', 'war_room_tracker', 'war_room_tracker_step'):
        if _has_table(table):
            op.drop_table(table)
