"""增加产品现货状态"""
from alembic import op
import sqlalchemy as sa

revision = "0005_product_stock"
down_revision = "0004_order_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("is_in_stock", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("products", "is_in_stock")
