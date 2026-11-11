"""catalog_city_options：客户可选城市码与 IP 段，订单记录客户选择

Revision ID: 0030_city_options
Revises: 0029_catalog_listings
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_city_options"
down_revision = "0029_catalog_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_city_options",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_category", sa.String(length=50), nullable=False),
        sa.Column("country_code", sa.String(length=10), nullable=False),
        sa.Column("city_code", sa.String(length=32), nullable=False),
        sa.Column("city_name", sa.String(length=80), nullable=False),
        sa.Column("ip_segments", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("data_source", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_category", "country_code", "city_code", name="uq_catalog_city_option"),
    )
    op.create_index("ix_catalog_city_options_resource_category", "catalog_city_options", ["resource_category"])
    op.create_index("ix_catalog_city_options_country_code", "catalog_city_options", ["country_code"])
    op.create_index("ix_catalog_city_options_is_enabled", "catalog_city_options", ["is_enabled"])

    # 客户指定的城市 / IP 段：空值表示不指定（交付时自动分配）。
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("requested_city_code", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("requested_city_name", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("requested_ip_segment", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("requested_ip_segment")
        batch.drop_column("requested_city_name")
        batch.drop_column("requested_city_code")

    op.drop_index("ix_catalog_city_options_is_enabled", table_name="catalog_city_options")
    op.drop_index("ix_catalog_city_options_country_code", table_name="catalog_city_options")
    op.drop_index("ix_catalog_city_options_resource_category", table_name="catalog_city_options")
    op.drop_table("catalog_city_options")
