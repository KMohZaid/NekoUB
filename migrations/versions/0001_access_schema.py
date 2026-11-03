"""Create sudo, share, and command grant tables."""

from alembic import op
import sqlalchemy as sa

revision = "0001_access_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sudoers",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
    )
    op.create_table(
        "share_users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
    )
    op.create_table(
        "allowed_cmds",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "command"),
    )


def downgrade() -> None:
    op.drop_table("allowed_cmds")
    op.drop_table("share_users")
    op.drop_table("sudoers")
