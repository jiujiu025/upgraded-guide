"""增加续费完成时间和订单多线路交付关系"""
from alembic import op
import sqlalchemy as sa

revision = "0007_renewal_multi_delivery"
down_revision = "0006_order_region_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivered_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("renewal_completed_at", sa.DateTime(), nullable=True))
    op.add_column("customer_lines", sa.Column("order_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("customer_lines") as batch:
        batch.create_foreign_key("fk_customer_lines_order_id_orders", "orders", ["order_id"], ["id"])
        batch.create_index("ix_customer_lines_order_id", ["order_id"])

    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE customer_lines SET order_id = "
        "(SELECT orders.id FROM orders WHERE orders.delivered_line_id = customer_lines.id) "
        "WHERE EXISTS (SELECT 1 FROM orders WHERE orders.delivered_line_id = customer_lines.id)"
    ))
    bind.execute(sa.text(
        "UPDATE orders SET delivered_quantity = 1 "
        "WHERE delivered_line_id IS NOT NULL"
    ))
    bind.execute(sa.text(
        "UPDATE orders SET status = 'partially_delivered' "
        "WHERE delivered_line_id IS NOT NULL AND quantity > 1 AND status = 'delivered'"
    ))
    bind.execute(sa.text(
        "UPDATE orders SET delivered_quantity = quantity "
        "WHERE status = 'delivered' AND delivered_line_id IS NULL"
    ))


def downgrade() -> None:
    with op.batch_alter_table("customer_lines") as batch:
        batch.drop_index("ix_customer_lines_order_id")
        batch.drop_constraint("fk_customer_lines_order_id_orders", type_="foreignkey")
    op.drop_column("customer_lines", "order_id")
    op.drop_column("orders", "renewal_completed_at")
    op.drop_column("orders", "delivered_quantity")
