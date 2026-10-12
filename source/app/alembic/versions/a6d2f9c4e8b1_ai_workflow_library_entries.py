"""AI workflows — library entries published by users.

`ai_workflow_library_entry`: workflow documents users published to the
library, listed next to the shipped ones.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install.

Revision ID: a6d2f9c4e8b1
Revises: e1f4b8c2a6d9
Create Date: 2026-10-12 09:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'a6d2f9c4e8b1'
down_revision = 'e1f4b8c2a6d9'
branch_labels = None
depends_on = None

_TABLE = 'ai_workflow_library_entry'
_INDEXES = (
    ('ix_ai_workflow_library_entry_owner', ['owner_id']),
    ('ix_ai_workflow_library_entry_name', ['name']),
)


def upgrade():
    if not _has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('category', sa.String(length=64), nullable=True),
            sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column('owner_id', sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    for name, columns in _INDEXES:
        if not index_exists(_TABLE, name):
            op.create_index(name, _TABLE, columns)


def downgrade():
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
