#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Index the asset registry on its default listing order.

- `idx_managed_asset_name_id` on `managed_asset (name, managed_asset_id)`:
  Manage > Assets lists by name with the id as tiebreak, which without
  an index sorts the whole registry (and evaluates the row visibility of
  every row) for each page.

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the index.

Revision ID: b5d8e2a4c7f1
Revises: f3c9a1d7b5e2
Create Date: 2026-10-23
"""
from alembic import op

from app.alembic.alembic_utils import index_exists


revision = 'b5d8e2a4c7f1'
down_revision = 'f3c9a1d7b5e2'
branch_labels = None
depends_on = None


def upgrade():
    if not index_exists('managed_asset', 'idx_managed_asset_name_id'):
        op.create_index('idx_managed_asset_name_id', 'managed_asset', ['name', 'managed_asset_id'])


def downgrade():
    if index_exists('managed_asset', 'idx_managed_asset_name_id'):
        op.drop_index('idx_managed_asset_name_id', table_name='managed_asset')
