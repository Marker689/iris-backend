"""AI workflows — a last fire per schedule.

A workflow may hold several trigger nodes, each schedule with its own
cron: `ai_workflow.cron_fired_at` ({trigger node id: ISO datetime}) keeps
the last fire of each, `last_fired_at` stays the last fire of any.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: e1f4a7c3b9d2
Revises: d8b3e6f1a5c2
Create Date: 2026-10-13 18:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'e1f4a7c3b9d2'
down_revision = 'd8b3e6f1a5c2'
branch_labels = None
depends_on = None

_TABLE = 'ai_workflow'


def upgrade():
    if _has_table(_TABLE) and not _table_has_column(_TABLE, 'cron_fired_at'):
        op.add_column(_TABLE, sa.Column('cron_fired_at', JSONB, nullable=False, server_default='{}'))


def downgrade():
    if _has_table(_TABLE) and _table_has_column(_TABLE, 'cron_fired_at'):
        op.drop_column(_TABLE, 'cron_fired_at')
