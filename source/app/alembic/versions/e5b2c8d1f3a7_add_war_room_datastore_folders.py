#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Folders in the war-room datastore.

Adds `war_room_datastore_path`, the war-room twin of the case
`datastore_path` (one root folder per war room, the others hanging off it),
and `war_room_datastore_file.file_parent_id`, the folder holding a file.

No backfill: the root folder of a war room is created the first time its
datastore is read, and files whose `file_parent_id` is NULL show in it.

Idempotent via `_has_table` / `_table_has_column` / `index_exists`:
`db.create_all()` builds the same objects from the models on a fresh
install, before the migrations run.

Revision ID: e5b2c8d1f3a7
Revises: c3e8f1a4d726
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column
from app.alembic.alembic_utils import index_exists


revision = 'e5b2c8d1f3a7'
down_revision = 'c3e8f1a4d726'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('war_room_datastore_path'):
        op.create_table(
            'war_room_datastore_path',
            sa.Column('path_id', sa.BigInteger(), primary_key=True),
            sa.Column('war_room_id', sa.BigInteger(), nullable=False),
            sa.Column('path_name', sa.Text(), nullable=False),
            sa.Column('path_parent_id', sa.BigInteger(), nullable=True),
            sa.Column('path_is_root', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.ForeignKeyConstraint(['war_room_id'], ['war_room.war_room_id'],
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['path_parent_id'], ['war_room_datastore_path.path_id'],
                                    ondelete='CASCADE'),
        )
    if not index_exists('war_room_datastore_path', 'ix_war_room_datastore_path_war_room_id'):
        op.create_index('ix_war_room_datastore_path_war_room_id',
                        'war_room_datastore_path', ['war_room_id'])

    if not _table_has_column('war_room_datastore_file', 'file_parent_id'):
        op.add_column('war_room_datastore_file',
                      sa.Column('file_parent_id', sa.BigInteger(), nullable=True))
        op.create_foreign_key('war_room_datastore_file_file_parent_id_fkey',
                              'war_room_datastore_file', 'war_room_datastore_path',
                              ['file_parent_id'], ['path_id'], ondelete='SET NULL')
    if not index_exists('war_room_datastore_file', 'ix_war_room_datastore_file_file_parent_id'):
        op.create_index('ix_war_room_datastore_file_file_parent_id',
                        'war_room_datastore_file', ['file_parent_id'])


def downgrade():
    if index_exists('war_room_datastore_file', 'ix_war_room_datastore_file_file_parent_id'):
        op.drop_index('ix_war_room_datastore_file_file_parent_id',
                      table_name='war_room_datastore_file')
    if _table_has_column('war_room_datastore_file', 'file_parent_id'):
        op.drop_column('war_room_datastore_file', 'file_parent_id')
    if _has_table('war_room_datastore_path'):
        op.drop_table('war_room_datastore_path')
