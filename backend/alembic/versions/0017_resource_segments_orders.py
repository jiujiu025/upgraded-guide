"""保存供应商网段快照与管理员订单来源"""

from alembic import op
import sqlalchemy as sa


revision = "0017_resource_segments_orders"
down_revision = "0016_supplier_credential"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("supplier_products", sa.Column("resource_category", sa.String(length=50), nullable=True))
    op.create_index("ix_supplier_products_resource_category", "supplier_products", ["resource_category"])

    op.create_table(
        "supplier_ip_segment_snapshots",
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("supplier_product_id", sa.Integer(), nullable=False),
        sa.Column("supplier_region_id", sa.Integer(), nullable=True),
        sa.Column("sync_run_id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=10), nullable=False),
        sa.Column("city_name", sa.String(length=160), nullable=True),
        sa.Column("cidr", sa.String(length=64), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("duration_code", sa.Integer(), nullable=True),
        sa.Column("is_segment_sufficient", sa.Boolean(), nullable=False),
        sa.Column("is_request_sufficient", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["supplier_product_id"], ["supplier_products.id"]),
        sa.ForeignKeyConstraint(["supplier_region_id"], ["supplier_regions.id"]),
        sa.ForeignKeyConstraint(["sync_run_id"], ["supplier_sync_runs.id"]),
        sa.UniqueConstraint("sync_run_id", "cidr", name="uq_supplier_segment_run_cidr"),
    )
    op.create_index("ix_supplier_ip_segment_snapshots_supplier_id", "supplier_ip_segment_snapshots", ["supplier_id"])
    op.create_index("ix_supplier_ip_segment_snapshots_supplier_product_id", "supplier_ip_segment_snapshots", ["supplier_product_id"])
    op.create_index("ix_supplier_ip_segment_snapshots_supplier_region_id", "supplier_ip_segment_snapshots", ["supplier_region_id"])
    op.create_index("ix_supplier_ip_segment_snapshots_sync_run_id", "supplier_ip_segment_snapshots", ["sync_run_id"])
    op.create_index("ix_supplier_ip_segment_snapshots_country_code", "supplier_ip_segment_snapshots", ["country_code"])
    op.create_index("ix_supplier_ip_segment_snapshots_checked_at", "supplier_ip_segment_snapshots", ["checked_at"])

    op.add_column("orders", sa.Column("source_type", sa.String(length=30), server_default="owned", nullable=False))
    op.add_column("orders", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("orders", sa.Column("resource_category", sa.String(length=50), nullable=True))
    op.add_column("orders", sa.Column("supplier_product_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("supplier_region_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("supplier_segment_snapshot_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("supplier_quote_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("supplier_cost", sa.Numeric(18, 4), nullable=True))
    op.create_index("ix_orders_source_type", "orders", ["source_type"])
    op.create_index("ix_orders_idempotency_key", "orders", ["idempotency_key"], unique=True)
    op.create_index("ix_orders_supplier_product_id", "orders", ["supplier_product_id"])
    op.create_index("ix_orders_supplier_region_id", "orders", ["supplier_region_id"])
    op.create_index("ix_orders_supplier_segment_snapshot_id", "orders", ["supplier_segment_snapshot_id"])
    op.create_index("ix_orders_supplier_quote_id", "orders", ["supplier_quote_id"])
    with op.batch_alter_table("orders") as batch_op:
        batch_op.create_foreign_key("fk_orders_supplier_product_id", "supplier_products", ["supplier_product_id"], ["id"])
        batch_op.create_foreign_key("fk_orders_supplier_region_id", "supplier_regions", ["supplier_region_id"], ["id"])
        batch_op.create_foreign_key("fk_orders_supplier_segment_snapshot_id", "supplier_ip_segment_snapshots", ["supplier_segment_snapshot_id"], ["id"])
        batch_op.create_foreign_key("fk_orders_supplier_quote_id", "supplier_quotes", ["supplier_quote_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("fk_orders_supplier_quote_id", type_="foreignkey")
        batch_op.drop_constraint("fk_orders_supplier_segment_snapshot_id", type_="foreignkey")
        batch_op.drop_constraint("fk_orders_supplier_region_id", type_="foreignkey")
        batch_op.drop_constraint("fk_orders_supplier_product_id", type_="foreignkey")
    op.drop_index("ix_orders_supplier_quote_id", table_name="orders")
    op.drop_index("ix_orders_supplier_segment_snapshot_id", table_name="orders")
    op.drop_index("ix_orders_supplier_region_id", table_name="orders")
    op.drop_index("ix_orders_supplier_product_id", table_name="orders")
    op.drop_index("ix_orders_source_type", table_name="orders")
    op.drop_index("ix_orders_idempotency_key", table_name="orders")
    op.drop_column("orders", "supplier_cost")
    op.drop_column("orders", "supplier_quote_id")
    op.drop_column("orders", "supplier_segment_snapshot_id")
    op.drop_column("orders", "supplier_region_id")
    op.drop_column("orders", "supplier_product_id")
    op.drop_column("orders", "resource_category")
    op.drop_column("orders", "source_type")
    op.drop_column("orders", "idempotency_key")
    op.drop_index("ix_supplier_ip_segment_snapshots_checked_at", table_name="supplier_ip_segment_snapshots")
    op.drop_index("ix_supplier_ip_segment_snapshots_country_code", table_name="supplier_ip_segment_snapshots")
    op.drop_index("ix_supplier_ip_segment_snapshots_sync_run_id", table_name="supplier_ip_segment_snapshots")
    op.drop_index("ix_supplier_ip_segment_snapshots_supplier_region_id", table_name="supplier_ip_segment_snapshots")
    op.drop_index("ix_supplier_ip_segment_snapshots_supplier_product_id", table_name="supplier_ip_segment_snapshots")
    op.drop_index("ix_supplier_ip_segment_snapshots_supplier_id", table_name="supplier_ip_segment_snapshots")
    op.drop_table("supplier_ip_segment_snapshots")
    op.drop_index("ix_supplier_products_resource_category", table_name="supplier_products")
    op.drop_column("supplier_products", "resource_category")
