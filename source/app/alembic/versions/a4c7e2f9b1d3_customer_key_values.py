"""Customers — free key/values.

Adds `client.key_values`, a JSON object of string keys to string values
(a customer's tenant ID, site code...), read by the API, the UI and the
`iris_customers_get` MCP tool.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: a4c7e2f9b1d3
Revises: 0c792729f4a3
Create Date: 2026-10-14 14:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'a4c7e2f9b1d3'
down_revision = '0c792729f4a3'
branch_labels = None
depends_on = None

_TABLE = 'client'
_COLUMN = 'key_values'


def upgrade():
    if _has_table(_TABLE) and not _table_has_column(_TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, JSON, nullable=True))


def downgrade():
    if _has_table(_TABLE) and _table_has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
