#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""More `not-affected` justifications on findings: `version_not_affected`,
`saas_deployment`, `on_prem_deployment`.

Rewrites the justification check of both finding tables. Idempotent:
`db.create_all()` runs before the migrations on a fresh install.

Revision ID: b7e4d2a9c5f1
Revises: c3f8a1d6e9b2
Create Date: 2026-10-16 14:00:00.000000
"""
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = 'b7e4d2a9c5f1'
down_revision = 'c3f8a1d6e9b2'
branch_labels = None
depends_on = None

_TABLES = (
    ('case_asset_vulnerability', 'ck_case_asset_vuln_justification'),
    ('managed_asset_vulnerability', 'ck_managed_asset_vuln_justification'),
)

_PREVIOUS = ('component_not_present', 'vulnerable_code_not_present', 'vulnerable_code_not_in_execute_path',
             'vulnerable_code_cannot_be_controlled_by_adversary', 'inline_mitigations_already_exist')
_ADDED = ('version_not_affected', 'saas_deployment', 'on_prem_deployment')


def _sql_list(values):
    return ','.join(f"'{value}'" for value in values)


def _set_check(justifications):
    for table, constraint in _TABLES:
        if not _has_table(table):
            continue
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}')
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ('
                   f'not_affected_justification IS NULL OR '
                   f'not_affected_justification IN ({_sql_list(justifications)}))')


def upgrade():
    _set_check(_PREVIOUS + _ADDED)


def downgrade():
    for table, _ in _TABLES:
        if _has_table(table):
            op.execute(f'UPDATE {table} SET not_affected_justification = NULL '
                       f'WHERE not_affected_justification IN ({_sql_list(_ADDED)})')
    _set_check(_PREVIOUS)
