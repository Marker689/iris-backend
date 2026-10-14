"""Server settings — proxy exceptions.

Adds `server_settings.no_proxy`, the "No proxy for" list of the proxy
settings: hosts, domains and networks the backend reaches directly
(see `app.iris_engine.utils.proxy_exceptions`).

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: b7d2e5a8c4f1
Revises: a4c7e2f9b1d3
Create Date: 2026-10-14 16:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'b7d2e5a8c4f1'
down_revision = 'a4c7e2f9b1d3'
branch_labels = None
depends_on = None

_TABLE = 'server_settings'
_COLUMN = 'no_proxy'


def upgrade():
    if _has_table(_TABLE) and not _table_has_column(_TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text, nullable=True))


def downgrade():
    if _has_table(_TABLE) and _table_has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
