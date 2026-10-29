"""增加按分类、国家和周期管理的客户销售价"""

from datetime import datetime
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

from app.data.retail_price_seed import RETAIL_PRICE_GROUPS, RETAIL_PRICE_SOURCE


revision = "0019_retail_price_book"
down_revision = "0018_finance_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retail_price_rules",
        sa.Column("resource_category", sa.String(length=50), nullable=False),
        sa.Column("country_code", sa.String(length=10), nullable=False),
        sa.Column("billing_days", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        sa.Column("source_version", sa.String(length=100), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("billing_days >= 30", name="ck_retail_price_minimum_30_days"),
        sa.CheckConstraint("unit_price > 0", name="ck_retail_price_positive"),
        sa.UniqueConstraint("resource_category", "country_code", "billing_days",
                            name="uq_retail_price_category_country_days"),
    )
    op.create_index("ix_retail_price_rules_resource_category", "retail_price_rules", ["resource_category"])
    op.create_index("ix_retail_price_rules_country_code", "retail_price_rules", ["country_code"])

    now = datetime.utcnow()
    rows = [
        {
            "resource_category": category,
            "country_code": country_code,
            "billing_days": billing_days,
            "unit_price": Decimal(price),
            "currency": "CNY",
            "source_version": RETAIL_PRICE_SOURCE,
            "created_at": now,
            "updated_at": now,
        }
        for category, country_code, prices in RETAIL_PRICE_GROUPS
        for billing_days, price in prices
    ]
    op.get_bind().execute(sa.table(
        "retail_price_rules",
        sa.column("resource_category", sa.String),
        sa.column("country_code", sa.String),
        sa.column("billing_days", sa.Integer),
        sa.column("unit_price", sa.Numeric(18, 2)),
        sa.column("currency", sa.String),
        sa.column("source_version", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    ).insert(), rows)


def downgrade() -> None:
    op.drop_index("ix_retail_price_rules_country_code", table_name="retail_price_rules")
    op.drop_index("ix_retail_price_rules_resource_category", table_name="retail_price_rules")
    op.drop_table("retail_price_rules")
