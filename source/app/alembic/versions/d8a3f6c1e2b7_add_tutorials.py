"""Add tutorial table for admin-authored guided tutorials.

A `tutorial` row is a guided walkthrough written by a server administrator
(Settings → Tutorials). Published rows join the built-in catalogue every
user sees under Help → Tutorials. A row with `builtin_id` is an
administrator's version of a built-in tutorial: it replaces it when
published, and hides it when not.

Idempotent via `_has_table` / `_table_has_column`.

Revision ID: d8a3f6c1e2b7
Revises: b7e4d2a9c5f1
Create Date: 2026-10-19 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'd8a3f6c1e2b7'
down_revision = 'b7e4d2a9c5f1'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('tutorial'):
        op.create_table(
            'tutorial',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('summary', sa.Text(), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('duration', sa.String(length=32), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
            sa.Column('published', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('created_by', sa.BigInteger(), nullable=True),
            sa.Column('builtin_id', sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(['created_by'], ['user.id'],
                                    ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('builtin_id', name='tutorial_builtin_id_key'),
        )
    elif not _table_has_column('tutorial', 'builtin_id'):
        op.add_column('tutorial', sa.Column('builtin_id', sa.String(length=64), nullable=True))
        op.create_unique_constraint('tutorial_builtin_id_key', 'tutorial', ['builtin_id'])


def downgrade():
    if _has_table('tutorial'):
        op.drop_table('tutorial')
