"""Categories of war rooms and AI workflows.

- `object_category`: one flat list per kind (`war_room`, `ai_workflow`),
  names unique within a kind whatever the case.
- `war_room.category_id` and `ai_workflow.category_id`: the category the
  object is filed in, set to null when the category is deleted.

Idempotent via `_has_table` / `_table_has_column`: `db.create_all()`
runs before the migrations on a fresh install.

Revision ID: e5b2c8d1f4a7
Revises: b7d2e5a8c4f1
Create Date: 2026-10-15 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column
from app.alembic.alembic_utils import index_exists


revision = 'e5b2c8d1f4a7'
down_revision = 'b7d2e5a8c4f1'
branch_labels = None
depends_on = None

_TABLE = 'object_category'
_FILED = ('war_room', 'ai_workflow')


def upgrade():
    if not _has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('kind', sa.String(32), nullable=False),
            sa.Column('name', sa.String(128), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('created_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        )
    if not index_exists(_TABLE, 'uq_object_category_kind_name'):
        op.create_index('uq_object_category_kind_name', _TABLE, ['kind', sa.text('lower(name)')], unique=True)

    for table in _FILED:
        if _has_table(table) and not _table_has_column(table, 'category_id'):
            op.add_column(table, sa.Column('category_id', sa.BigInteger(),
                                           sa.ForeignKey(f'{_TABLE}.id', ondelete='SET NULL'), nullable=True))
        index = f'ix_{table}_category_id'
        if _has_table(table) and not index_exists(table, index):
            op.create_index(index, table, ['category_id'])


def downgrade():
    for table in _FILED:
        if _has_table(table) and _table_has_column(table, 'category_id'):
            op.drop_column(table, 'category_id')
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
