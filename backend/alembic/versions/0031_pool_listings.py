"""catalog_listings 增加池维度：官网可见性改为「池 × 产品线 × 国家」人工上架

Revision ID: 0031_pool_listings
Revises: 0030_city_options
Create Date: 2026-11-10

规则变更：以前"有报价即上架"，现在"没有任何记录 = 下架"，完全由管理员决定。
为了上线瞬间官网不空白，这里把**迁移时在售的格子**写成 `pool_id=NULL + listed`
（平台级上架，对所有资源池生效）。此后新增的国家/商品默认不可见，必须人工上架。
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "0031_pool_listings"
down_revision = "0030_city_options"
branch_labels = None
depends_on = None


# 与 app.data.site_catalog.CATEGORY_TO_LINE 保持一致（迁移里不 import 业务代码，
# 避免以后业务映射调整导致老迁移跑不起来）。
CATEGORY_TO_LINE = {
    "static_residential": "residential",
    "static_residential_native": "residential",
    "static_residential_isp": "residential",
    "static_datacenter": "residential",
    "social_media": "social",
    "live_stream_mobile": "live",
    "live_stream_pc": "live",
}


def upgrade() -> None:
    with op.batch_alter_table("catalog_listings") as batch:
        batch.add_column(sa.Column("pool_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_catalog_listings_pool_id", "resource_pools", ["pool_id"], ["id"])
    op.create_index("ix_catalog_listings_pool_id", "catalog_listings", ["pool_id"])

    # 旧的 (line_code, region_id) 唯一约束要换成分池的部分唯一索引
    with op.batch_alter_table("catalog_listings") as batch:
        batch.drop_constraint("uq_catalog_listing_line_region", type_="unique")

    op.create_index(
        "uq_catalog_listing_global_cell", "catalog_listings", ["line_code", "region_id"],
        unique=True, sqlite_where=sa.text("pool_id IS NULL"), postgresql_where=sa.text("pool_id IS NULL"),
    )
    op.create_index(
        "uq_catalog_listing_pool_cell", "catalog_listings",
        ["pool_id", "line_code", "region_id"],
        unique=True, sqlite_where=sa.text("pool_id IS NOT NULL"),
        postgresql_where=sa.text("pool_id IS NOT NULL"),
    )

    # 回填：把当前"有商品在售"的格子落成平台级 listed，避免改成人工上架后官网一夜清空。
    conn = op.get_bind()
    product_rows = conn.execute(sa.text(
        "SELECT DISTINCT resource_category, region_id FROM products "
        "WHERE is_active = :active AND resource_category IS NOT NULL"
    ), {"active": True}).fetchall()
    existing = {
        (line_code, region_id): status
        for line_code, region_id, status in conn.execute(sa.text(
            "SELECT line_code, region_id, status FROM catalog_listings WHERE pool_id IS NULL"
        )).fetchall()
    }
    now = datetime.utcnow()
    inserted = 0
    for category, region_id in product_rows:
        line_code = CATEGORY_TO_LINE.get(category)
        if not line_code:
            continue
        key = (line_code, region_id)
        if key in existing:
            continue  # 已有记录（含人工下架）保持原样
        conn.execute(sa.text(
            "INSERT INTO catalog_listings "
            "(pool_id, line_code, region_id, status, reason, updated_by, created_at, updated_at) "
            "VALUES (NULL, :line_code, :region_id, 'listed', :reason, NULL, :now, :now)"
        ), {
            "line_code": line_code, "region_id": region_id,
            "reason": "迁移：保持改造前的在售状态", "now": now,
        })
        # 同一 (line, region) 可能被多个分类映射到（直播手机/电脑都归 live），
        # 插过的要记账，否则第二次插入撞部分唯一索引。
        existing[key] = "listed"
        inserted += 1
    print(f"回填平台级上架 {inserted} 个格子")


def downgrade() -> None:
    conn = op.get_bind()
    # 池级记录在旧模型里没有位置，先删掉（全局记录保留）
    conn.execute(sa.text("DELETE FROM catalog_listings WHERE pool_id IS NOT NULL"))

    op.drop_index("uq_catalog_listing_pool_cell", table_name="catalog_listings")
    op.drop_index("uq_catalog_listing_global_cell", table_name="catalog_listings")
    op.drop_index("ix_catalog_listings_pool_id", table_name="catalog_listings")

    with op.batch_alter_table("catalog_listings") as batch:
        batch.create_unique_constraint(
            "uq_catalog_listing_line_region", ["line_code", "region_id"],
        )
        batch.drop_constraint("fk_catalog_listings_pool_id", type_="foreignkey")
        batch.drop_column("pool_id")
