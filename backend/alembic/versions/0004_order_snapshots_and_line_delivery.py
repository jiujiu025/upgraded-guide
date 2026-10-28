"""增加订单快照字段和线路交付数据槽位"""
from alembic import op
import sqlalchemy as sa

revision = "0004_order_snapshots"
down_revision = "0003_user_login_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("orders", sa.Column("billing_days", sa.Integer(), nullable=False, server_default="30"))
    op.add_column("orders", sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("region_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("product_name", sa.String(120), nullable=False, server_default=""))
    op.add_column("orders", sa.Column("payment_status", sa.String(30), nullable=False, server_default="unpaid"))
    op.add_column("orders", sa.Column("assigned_sales_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("confirmed_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("order_type", sa.String(20), nullable=False, server_default="purchase"))
    op.add_column("orders", sa.Column("renewal_line_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("orders") as batch:
        batch.create_foreign_key("fk_orders_region_id_regions", "regions", ["region_id"], ["id"])
        batch.create_foreign_key("fk_orders_assigned_sales_id_users", "users", ["assigned_sales_id"], ["id"])
        batch.create_foreign_key("fk_orders_renewal_line_id_customer_lines", "customer_lines", ["renewal_line_id"], ["id"])
        batch.create_index("ix_orders_assigned_sales_id", ["assigned_sales_id"])
        batch.create_index("ix_orders_renewal_line_id", ["renewal_line_id"])

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE orders SET unit_price = amount, total_amount = amount WHERE unit_price = 0 AND total_amount = 0"))
    bind.execute(sa.text("UPDATE orders SET product_name = (SELECT name FROM products WHERE products.id = orders.product_id) WHERE product_name = ''"))
    bind.execute(sa.text("UPDATE orders SET region_id = (SELECT region_id FROM products WHERE products.id = orders.product_id) WHERE region_id IS NULL"))
    bind.execute(sa.text("UPDATE orders SET assigned_sales_id = (SELECT sales_id FROM customers WHERE customers.id = orders.customer_id) WHERE assigned_sales_id IS NULL"))

    op.add_column("customer_lines", sa.Column("node_link", sa.Text(), nullable=True))
    op.add_column("customer_lines", sa.Column("subscription_url", sa.Text(), nullable=True))
    op.add_column("customer_lines", sa.Column("node_qr_data", sa.Text(), nullable=True))
    op.add_column("customer_lines", sa.Column("subscription_qr_data", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("customer_lines", "subscription_qr_data")
    op.drop_column("customer_lines", "node_qr_data")
    op.drop_column("customer_lines", "subscription_url")
    op.drop_column("customer_lines", "node_link")
    with op.batch_alter_table("orders") as batch:
        batch.drop_index("ix_orders_renewal_line_id")
        batch.drop_index("ix_orders_assigned_sales_id")
        batch.drop_constraint("fk_orders_renewal_line_id_customer_lines", type_="foreignkey")
        batch.drop_constraint("fk_orders_assigned_sales_id_users", type_="foreignkey")
        batch.drop_constraint("fk_orders_region_id_regions", type_="foreignkey")
    for column in ["renewal_line_id", "order_type", "cancelled_at", "delivered_at", "confirmed_at", "assigned_sales_id", "payment_status", "product_name", "region_id", "total_amount", "unit_price", "billing_days", "quantity"]:
        op.drop_column("orders", column)
