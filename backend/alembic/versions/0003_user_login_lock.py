"""增加登录锁定时间"""
from alembic import op
import sqlalchemy as sa

revision = "0003_user_login_lock"
down_revision = "0002_customer_user_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("locked_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "locked_until")
