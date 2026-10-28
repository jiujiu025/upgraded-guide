"""新增资源池 1 只读供应商目录、映射与快照表"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0010_supplier_readonly"
down_revision = "0009_remove_delivery_fk"
branch_labels = None
depends_on = None


def _timestamps() -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return {"created_at": now, "updated_at": now}


def _create_indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    timestamps = (
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("adapter_type", sa.String(80), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("secret_ref", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("auto_purchase_enabled", sa.Boolean(), nullable=False),
        *timestamps,
    )
    op.create_index("ix_suppliers_code", "suppliers", ["code"], unique=True)
    op.create_index("ix_suppliers_status", "suppliers", ["status"])
    op.create_table(
        "resource_pools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("strategy", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_resource_pools_code", "resource_pools", ["code"], unique=True)
    op.create_index("ix_resource_pools_status", "resource_pools", ["status"])
    op.create_table(
        "resource_pool_suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pool_id", sa.Integer(), sa.ForeignKey("resource_pools.id"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("pool_id", "supplier_id", name="uq_resource_pool_supplier"),
    )
    _create_indexes("resource_pool_suppliers", ["pool_id", "supplier_id"])
    op.create_table(
        "supplier_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("external_product_ref", sa.String(80), nullable=False),
        sa.Column("name_snapshot", sa.String(200), nullable=False),
        sa.Column("suffix", sa.String(50), nullable=True),
        sa.Column("local_product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("source_endpoint", sa.String(255), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("supplier_id", "external_product_ref", name="uq_supplier_product_ref"),
    )
    _create_indexes("supplier_products", ["supplier_id", "local_product_id", "is_available"])
    op.create_table(
        "supplier_regions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_product_id", sa.Integer(), sa.ForeignKey("supplier_products.id"), nullable=False),
        sa.Column("external_country_ref", sa.String(80), nullable=False),
        sa.Column("country_code", sa.String(10), nullable=False),
        sa.Column("country_name", sa.String(120), nullable=False),
        sa.Column("external_city_ref", sa.String(160), nullable=False),
        sa.Column("city_name", sa.String(160), nullable=True),
        sa.Column("local_region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("source_endpoint", sa.String(255), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("supplier_product_id", "external_country_ref", "external_city_ref", name="uq_supplier_product_region_ref"),
    )
    _create_indexes("supplier_regions", ["supplier_product_id", "country_code", "local_region_id", "is_available"])
    op.create_table(
        "product_supply_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=False),
        sa.Column("pool_id", sa.Integer(), sa.ForeignKey("resource_pools.id"), nullable=False),
        sa.Column("supplier_product_id", sa.Integer(), sa.ForeignKey("supplier_products.id"), nullable=False),
        sa.Column("supplier_region_id", sa.Integer(), sa.ForeignKey("supplier_regions.id"), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("product_id", "region_id", "pool_id", name="uq_product_region_pool_rule"),
    )
    _create_indexes("product_supply_rules", ["product_id", "region_id", "pool_id", "supplier_product_id", "supplier_region_id", "is_enabled"])
    op.create_table(
        "supplier_quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("supplier_product_id", sa.Integer(), sa.ForeignKey("supplier_products.id"), nullable=True),
        sa.Column("external_product_ref", sa.String(80), nullable=False),
        sa.Column("country_code", sa.String(10), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("unit_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_custom", sa.Boolean(), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("source_endpoint", sa.String(255), nullable=False),
        sa.Column("quoted_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _create_indexes("supplier_quotes", ["supplier_id", "supplier_product_id", "external_product_ref", "country_code", "is_available", "quoted_at"])
    op.create_table(
        "supplier_inventory_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("supplier_product_id", sa.Integer(), sa.ForeignKey("supplier_products.id"), nullable=False),
        sa.Column("supplier_region_id", sa.Integer(), sa.ForeignKey("supplier_regions.id"), nullable=True),
        sa.Column("country_code", sa.String(10), nullable=False),
        sa.Column("city_name", sa.String(160), nullable=True),
        sa.Column("availability", sa.String(30), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("source_endpoint", sa.String(255), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _create_indexes("supplier_inventory_snapshots", ["supplier_id", "supplier_product_id", "supplier_region_id", "country_code", "availability", "checked_at"])

    now = _timestamps()
    op.bulk_insert(
        sa.table("suppliers", *[sa.column(name) for name in ["id", "code", "name", "adapter_type", "base_url", "secret_ref", "status", "auto_purchase_enabled", "created_at", "updated_at"]]),
        [{"id": 1, "code": "quanqiudaili", "name": "全球代理", "adapter_type": "global_proxy_readonly", "base_url": "https://admin.quanqiudaili.com", "secret_ref": "env:GLOBAL_PROXY_API_KEY", "status": "active", "auto_purchase_enabled": False, **now}],
    )
    op.bulk_insert(
        sa.table("resource_pools", *[sa.column(name) for name in ["id", "code", "name", "status", "strategy", "created_at", "updated_at"]]),
        [{"id": 1, "code": "global_proxy_pool", "name": "资源池 1", "status": "active", "strategy": "priority_manual_fallback", **now}],
    )
    op.bulk_insert(
        sa.table("resource_pool_suppliers", *[sa.column(name) for name in ["pool_id", "supplier_id", "priority", "is_enabled", "created_at", "updated_at"]]),
        [{"pool_id": 1, "supplier_id": 1, "priority": 1, "is_enabled": True, **now}],
    )


def downgrade() -> None:
    for table in [
        "supplier_inventory_snapshots", "supplier_quotes", "product_supply_rules", "supplier_regions",
        "supplier_products", "resource_pool_suppliers", "resource_pools", "suppliers",
    ]:
        op.drop_table(table)
