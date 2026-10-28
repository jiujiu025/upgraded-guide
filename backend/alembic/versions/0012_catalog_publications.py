"""新增人工审核目录发布版本"""

from alembic import op
import sqlalchemy as sa


revision = "0012_catalog_publications"
down_revision = "0011_supplier_sync_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supplier_product_id", sa.Integer(), sa.ForeignKey("supplier_products.id"), nullable=False),
        sa.Column("supplier_region_id", sa.Integer(), sa.ForeignKey("supplier_regions.id"), nullable=False),
        sa.Column("inventory_snapshot_id", sa.Integer(), sa.ForeignKey("supplier_inventory_snapshots.id"), nullable=False),
        sa.Column("quote_snapshot_id", sa.Integer(), sa.ForeignKey("supplier_quotes.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("withdrawn_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
        sa.Column("before_values", sa.JSON(), nullable=False),
        sa.Column("after_values", sa.JSON(), nullable=False),
        sa.Column("snapshot_version", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("product_id", "version", name="uq_catalog_publication_version"),
    )
    op.create_index("ix_catalog_publications_product_id", "catalog_publications", ["product_id"])
    op.create_index("ix_catalog_publications_region_id", "catalog_publications", ["region_id"])


def downgrade() -> None:
    op.drop_table("catalog_publications")
