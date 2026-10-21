#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""AI workflows shared with groups.

Adds `ai_workflow.shared_group_ids`, the groups whose members may see
and run a workflow they do not own (as themselves). Existing workflows
are shared with no one.

Idempotent via `_table_has_column`: `db.create_all()` builds the same
column from the model on a fresh install, before the migrations run.

Revision ID: b7e2d4a9c1f6
Revises: e5b2c8d1f3a7
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _table_has_column


revision = 'b7e2d4a9c1f6'
down_revision = 'e5b2c8d1f3a7'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('ai_workflow', 'shared_group_ids'):
        op.add_column('ai_workflow',
                      sa.Column('shared_group_ids', postgresql.JSONB(), nullable=False,
                                server_default=sa.text("'[]'::jsonb")))


def downgrade():
    if _table_has_column('ai_workflow', 'shared_group_ids'):
        op.drop_column('ai_workflow', 'shared_group_ids')
