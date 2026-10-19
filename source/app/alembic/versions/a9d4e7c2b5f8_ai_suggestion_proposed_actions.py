"""AI suggestions — several proposed actions in one suggestion.

`ai_suggestion.proposed_actions`: `[{id, label, tool, arguments, status,
tool_call_id, error}]`, the actions of a bundled suggestion the analyst
accepts all at once or a few of them. `proposed_action` keeps the single
action of the other suggestions.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: a9d4e7c2b5f8
Revises: e5b2c8d4f1a3
Create Date: 2026-10-19 18:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'a9d4e7c2b5f8'
down_revision = 'e5b2c8d4f1a3'
branch_labels = None
depends_on = None

_TABLE = 'ai_suggestion'
_COLUMN = 'proposed_actions'


def upgrade():
    if _has_table(_TABLE) and not _table_has_column(_TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, JSONB(), nullable=True))


def downgrade():
    if _has_table(_TABLE) and _table_has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
