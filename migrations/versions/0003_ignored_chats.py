"""Store chats where delegated users are not allowed to run commands."""

from alembic import op
import sqlalchemy as sa

revision = "0003_ignored_chats"
down_revision = "0002_role_user_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ignored_chats",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ignored_chats")
