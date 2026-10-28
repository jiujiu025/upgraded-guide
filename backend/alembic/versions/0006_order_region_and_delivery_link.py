"""增加订单地区快照和精确交付线路关联"""
from alembic import op
import sqlalchemy as sa

revision = "0006_order_region_delivery"
down_revision = "0005_product_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("region_name", sa.String(80), nullable=False, server_default=""))
    op.add_column("orders", sa.Column("delivered_line_id", sa.Integer(), nullable=True))
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE orders SET region_name = COALESCE((SELECT name FROM regions WHERE regions.id = orders.region_id), '')"))
    with op.batch_alter_table("orders") as batch:
        batch.create_foreign_key("fk_orders_delivered_line_id_customer_lines", "customer_lines", ["delivered_line_id"], ["id"])
        batch.create_index("ix_orders_delivered_line_id", ["delivered_line_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_index("ix_orders_delivered_line_id")
        batch.drop_constraint("fk_orders_delivered_line_id_customer_lines", type_="foreignkey")
    op.drop_column("orders", "delivered_line_id")
    op.drop_column("orders", "region_name")
