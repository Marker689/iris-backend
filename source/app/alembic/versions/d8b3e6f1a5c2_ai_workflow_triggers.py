"""AI workflows — several triggers per workflow.

`ai_workflow.triggers` ([{type, config}], at most one per type),
backfilled from the single `trigger_type` / `trigger_config`, which stay
as the first trigger.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: d8b3e6f1a5c2
Revises: c4e9a2f7b3d1
Create Date: 2026-10-13 14:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'd8b3e6f1a5c2'
down_revision = 'c4e9a2f7b3d1'
branch_labels = None
depends_on = None

_TABLE = 'ai_workflow'


def upgrade():
    if not _has_table(_TABLE):
        return
    if not _table_has_column(_TABLE, 'triggers'):
        op.add_column(_TABLE, sa.Column('triggers', JSONB, nullable=False, server_default='[]'))
    op.execute(sa.text(
        "UPDATE ai_workflow SET triggers = jsonb_build_array("
        "jsonb_build_object('type', trigger_type, 'config', coalesce(trigger_config, '{}'::jsonb))) "
        "WHERE triggers = '[]'::jsonb"))


def downgrade():
    if _has_table(_TABLE) and _table_has_column(_TABLE, 'triggers'):
        op.drop_column(_TABLE, 'triggers')
