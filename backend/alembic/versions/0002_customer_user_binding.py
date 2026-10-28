"""绑定客户登录账号，修复客户数据范围关联"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_customer_user_binding"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("customers")}
    if "user_id" not in columns:
        with op.batch_alter_table("customers") as batch:
            batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
            batch.create_index("ix_customers_user_id", ["user_id"], unique=True)
            batch.create_foreign_key("fk_customers_user_id_users", "users", ["user_id"], ["id"])


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("customers")}
    if "user_id" in columns:
        with op.batch_alter_table("customers") as batch:
            constraints = {item.get("name") for item in inspector.get_foreign_keys("customers")}
            indexes = {item.get("name") for item in inspector.get_indexes("customers")}
            if "fk_customers_user_id_users" in constraints:
                batch.drop_constraint("fk_customers_user_id_users", type_="foreignkey")
            if "ix_customers_user_id" in indexes:
                batch.drop_index("ix_customers_user_id")
            batch.drop_column("user_id")
