"""移除与唯一索引重复的 PostgreSQL 唯一约束。"""

from alembic import op

revision = "0014_pg_unique_cleanup"
down_revision = "0013_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_constraint("users_username_key", "users", type_="unique")
    op.drop_constraint("orders_order_no_key", "orders", type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.create_unique_constraint("users_username_key", "users", ["username"])
    op.create_unique_constraint("orders_order_no_key", "orders", ["order_no"])
