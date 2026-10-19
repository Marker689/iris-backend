"""Make write auto-approve a per-conversation opt-in the admin allows.

`chatbot_auto_approve_write_tools` (server settings) and
`auto_approve_write_tools` (chatbot policy) used to skip the
Approve/Deny card for every conversation. They now only *allow* it, so
they are renamed `chatbot_allow_auto_approve_write_tools` /
`allow_auto_approve_write_tools`. The analyst opts in per conversation
through the new `case_chat_conversation.auto_approve_write_tools`
column, off by default — Yuki keeps asking until they flip it.

Idempotent via `_table_has_column`.

Revision ID: e5b2c8d4f1a3
Revises: d8a3f6c1e2b7
Create Date: 2026-10-19 15:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'e5b2c8d4f1a3'
down_revision = 'd8a3f6c1e2b7'
branch_labels = None
depends_on = None


_RENAMES = (
    ('server_settings', 'chatbot_auto_approve_write_tools',
     'chatbot_allow_auto_approve_write_tools'),
    ('chatbot_policy', 'auto_approve_write_tools',
     'allow_auto_approve_write_tools'),
)


def _rename(table, old, new):
    if _table_has_column(table, old) and not _table_has_column(table, new):
        op.alter_column(table, old, new_column_name=new)


def upgrade():
    for table, old, new in _RENAMES:
        _rename(table, old, new)

    if not _table_has_column('case_chat_conversation', 'auto_approve_write_tools'):
        op.add_column(
            'case_chat_conversation',
            sa.Column(
                'auto_approve_write_tools',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('false'),
            ),
        )


def downgrade():
    if _table_has_column('case_chat_conversation', 'auto_approve_write_tools'):
        op.drop_column('case_chat_conversation', 'auto_approve_write_tools')

    for table, old, new in _RENAMES:
        _rename(table, new, old)
