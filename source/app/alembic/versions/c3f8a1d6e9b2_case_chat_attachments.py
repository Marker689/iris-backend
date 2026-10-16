"""Files sent to Yuki: `case_chat_attachment`.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install.

Revision ID: c3f8a1d6e9b2
Revises: e5b2c8d1f4a7
Create Date: 2026-10-16 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c3f8a1d6e9b2'
down_revision = 'e5b2c8d1f4a7'
branch_labels = None
depends_on = None

_TABLE = 'case_chat_attachment'
_INDEX = 'ix_case_chat_attachment_conversation_message'


def upgrade():
    if not _has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('conversation_id', sa.BigInteger(),
                      sa.ForeignKey('case_chat_conversation.id', ondelete='CASCADE'), nullable=False),
            sa.Column('message_id', sa.BigInteger(),
                      sa.ForeignKey('case_chat_message.id', ondelete='SET NULL'), nullable=True),
            sa.Column('user_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('filename', sa.String(255), nullable=False),
            sa.Column('kind', sa.String(16), nullable=False),
            sa.Column('media_type', sa.String(64), nullable=False),
            sa.Column('size', sa.Integer(), nullable=False),
            sa.Column('data', sa.LargeBinary(), nullable=False),
            sa.Column('staged_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        )
    if not index_exists(_TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ['conversation_id', 'message_id'])


def downgrade():
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
