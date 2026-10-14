"""Registry assets attached to a war room's scope.

- `war_room_managed_asset`: an Assets Manager asset in the scope of a
  war room, optionally linked to an attached case and/or a catalogue
  vulnerability. Unique on the four columns with NULLS NOT DISTINCT, so
  an asset has at most one unlinked attachment per room. Every foreign
  key cascades except the author.

Idempotent via `_has_table`: `db.create_all()` runs before the
migrations on a fresh install and already creates the table.

Revision ID: 0c792729f4a3
Revises: f3a8c1d6e2b4
Create Date: 2026-10-14 18:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = '0c792729f4a3'
down_revision = 'f3a8c1d6e2b4'
branch_labels = None
depends_on = None

_TABLE = 'war_room_managed_asset'


def upgrade():
    if _has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('war_room_id', sa.BigInteger(),
                  sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=False),
        sa.Column('managed_asset_id', sa.BigInteger(),
                  sa.ForeignKey('managed_asset.managed_asset_id', ondelete='CASCADE'), nullable=False),
        sa.Column('case_id', sa.BigInteger(),
                  sa.ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=True),
        sa.Column('vulnerability_id', sa.BigInteger(),
                  sa.ForeignKey('vulnerability.vulnerability_id', ondelete='CASCADE'), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('added_by_id', sa.BigInteger(),
                  sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('war_room_id', 'managed_asset_id', 'case_id', 'vulnerability_id',
                            name='uq_war_room_managed_asset', postgresql_nulls_not_distinct=True),
    )
    op.create_index('ix_war_room_managed_asset_war_room_id', _TABLE, ['war_room_id'])
    op.create_index('ix_war_room_managed_asset_managed_asset_id', _TABLE, ['managed_asset_id'])
    op.create_index('ix_war_room_managed_asset_vulnerability_id', _TABLE, ['vulnerability_id'])


def downgrade():
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
