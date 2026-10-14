"""Case chat — stop a running assistant turn and steer it mid-run.

Adds the live-run control columns to `case_chat_conversation`:
`run_token` / `run_heartbeat_at` mark the loop driving the conversation,
`interrupt_requested` asks it to stop, `queued_user_texts` parks user
turns sent while it runs.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: f3a8c1d6e2b4
Revises: e1f4a7c3b9d2
Create Date: 2026-10-14 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'f3a8c1d6e2b4'
down_revision = 'e1f4a7c3b9d2'
branch_labels = None
depends_on = None

_TABLE = 'case_chat_conversation'

_COLUMNS = (
    sa.Column('run_token', sa.String(36), nullable=True),
    sa.Column('run_heartbeat_at', sa.DateTime, nullable=True),
    sa.Column('interrupt_requested', sa.Boolean, nullable=False, server_default=sa.text('false')),
    sa.Column('queued_user_texts', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
)


def upgrade():
    if not _has_table(_TABLE):
        return
    for column in _COLUMNS:
        if not _table_has_column(_TABLE, column.name):
            op.add_column(_TABLE, column)


def downgrade():
    if not _has_table(_TABLE):
        return
    for column in _COLUMNS:
        if _table_has_column(_TABLE, column.name):
            op.drop_column(_TABLE, column.name)
