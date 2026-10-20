#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Lookup indexes on `user_case_effective_access` and `similar_alerts_cache`.

`user_case_effective_access` is read on almost every authenticated request
by `(user_id, case_id)` and had no index on either column, so each check
was a sequential scan of a table holding one row per user per case.

The index is deliberately not unique. The table does hold duplicate
`(user_id, case_id)` rows, and the readers disagree on which one wins, so
deduplicating them here would change who can open which case as a side
effect of an upgrade. The writers are also check-then-insert: under a
unique index a race that today leaves a harmless duplicate would fail the
request instead.

`similar_alerts_cache` is matched by asset name or IOC value on every alert
display and purged by `alert_id` on every alert update or delete. Both
value columns are unbounded `Text`, and a btree entry cannot exceed about
2.7 kB — one long command line stored as an IOC would make the build, and
from then on every insert carrying it, fail. Hash indexes have no such
limit and the lookups are all equality, so the value columns get those.

Idempotent via `index_exists`: `db.create_all()` builds the same indexes
from the models on a fresh install, before the migrations run.

Revision ID: c3e8f1a4d726
Revises: a9d4e7c2b5f8
Create Date: 2026-10-20
"""
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c3e8f1a4d726'
down_revision = 'a9d4e7c2b5f8'
branch_labels = None
depends_on = None

#: `(table, index name, columns, access method)`, mirroring the
#: `__table_args__` of `UserCaseEffectiveAccess` and `SimilarAlertsCache`.
_INDEXES = (
    ('user_case_effective_access', 'ix_user_case_effective_access_user_id_case_id', ['user_id', 'case_id'], None),
    ('similar_alerts_cache', 'ix_similar_alerts_cache_alert_id', ['alert_id'], None),
    ('similar_alerts_cache', 'ix_similar_alerts_cache_asset_name', ['asset_name'], 'hash'),
    ('similar_alerts_cache', 'ix_similar_alerts_cache_ioc_value', ['ioc_value'], 'hash'),
)


def upgrade():
    for table, name, columns, using in _INDEXES:
        if not _has_table(table) or index_exists(table, name):
            continue

        if using:
            op.create_index(name, table, columns, postgresql_using=using)
        else:
            op.create_index(name, table, columns)


def downgrade():
    for table, name, _columns, _using in _INDEXES:
        if _has_table(table) and index_exists(table, name):
            op.drop_index(name, table_name=table)
