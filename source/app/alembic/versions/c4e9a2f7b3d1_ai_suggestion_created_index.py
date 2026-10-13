"""AI suggestions — index for the newest-first paginated listing.

`ix_ai_suggestion_created` on (created_at, id): the order and the keyset
cursor of `GET /api/v2/ai-suggestions`.

Idempotent via `index_exists`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: c4e9a2f7b3d1
Revises: a6d2f9c4e8b1
Create Date: 2026-10-13 09:00:00.000000
"""
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c4e9a2f7b3d1'
down_revision = 'a6d2f9c4e8b1'
branch_labels = None
depends_on = None

_TABLE = 'ai_suggestion'
_INDEX = 'ix_ai_suggestion_created'


def upgrade():
    if _has_table(_TABLE) and not index_exists(_TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ['created_at', 'id'])


def downgrade():
    if _has_table(_TABLE) and index_exists(_TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
