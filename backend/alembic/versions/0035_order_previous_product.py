"""orders.previous_product_id：升级单记录升级前的产品。

为什么必须加：升级单的 ``product_id`` 存的是**目标**产品，退款时要回退线路却没地方查原产品。
不加这个字段只能从 remark 里抠字符串，或者靠「同国家同周期更低价的 SKU」反推——两者都会出错。
字段可空：只有升级单会写，其他订单保持 NULL。
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_order_previous_product"
down_revision = "0034_audit_actor_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("previous_product_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_orders_previous_product_id", "products",
            ["previous_product_id"], ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("fk_orders_previous_product_id", type_="foreignkey")
        batch.drop_column("previous_product_id")
