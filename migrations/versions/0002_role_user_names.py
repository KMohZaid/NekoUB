"""Store display names for delegated role users."""

from alembic import op
import sqlalchemy as sa

revision = "0002_role_user_names"
down_revision = "0001_access_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sudoers", sa.Column("first_name", sa.Text(), nullable=True))
    op.add_column("sudoers", sa.Column("last_name", sa.Text(), nullable=True))
    op.add_column("share_users", sa.Column("first_name", sa.Text(), nullable=True))
    op.add_column("share_users", sa.Column("last_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("share_users", "last_name")
    op.drop_column("share_users", "first_name")
    op.drop_column("sudoers", "last_name")
    op.drop_column("sudoers", "first_name")
