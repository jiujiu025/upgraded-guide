"""catalog_listings：官网「产品线 × 国家」可见性覆盖

Revision ID: 0029_catalog_listings
Revises: 0028_order_created_via
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_catalog_listings"
down_revision = "0028_order_created_via"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_code", sa.String(length=20), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("line_code", "region_id", name="uq_catalog_listing_line_region"),
    )
    op.create_index("ix_catalog_listings_line_code", "catalog_listings", ["line_code"])
    op.create_index("ix_catalog_listings_region_id", "catalog_listings", ["region_id"])
    op.create_index("ix_catalog_listings_status", "catalog_listings", ["status"])


def downgrade() -> None:
    op.drop_index("ix_catalog_listings_status", table_name="catalog_listings")
    op.drop_index("ix_catalog_listings_region_id", table_name="catalog_listings")
    op.drop_index("ix_catalog_listings_line_code", table_name="catalog_listings")
    op.drop_table("catalog_listings")
