"""移除订单首条交付线路的冗余外键，消除订单与线路循环依赖"""

from alembic import op


revision = "0009_remove_delivery_fk"
down_revision = "0008_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("fk_orders_delivered_line_id_customer_lines", type_="foreignkey")


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.create_foreign_key(
            "fk_orders_delivered_line_id_customer_lines",
            "customer_lines",
            ["delivered_line_id"],
            ["id"],
        )
