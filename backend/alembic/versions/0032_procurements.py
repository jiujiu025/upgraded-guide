"""procurements：付款后向上游采购静态住宅，并记录每一次尝试。

上游没有幂等键，采购失败不自动重试——重试就是重复扣款。所以这里只建表，
重试与补偿留给人工。
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_procurements"
down_revision = "0031_pool_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "procurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("supplier_code", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("external_order_no", sa.String(length=80), nullable=True),
        sa.Column("request_payload", sa.Text(), nullable=True),
        sa.Column("response_payload", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "line_index", name="uq_procurement_order_line"),
    )
    op.create_index("ix_procurements_order_id", "procurements", ["order_id"])
    op.create_index("ix_procurements_status", "procurements", ["status"])
    op.create_index("ix_procurements_supplier_id", "procurements", ["supplier_id"])
    op.create_index("ix_procurements_external_order_no", "procurements", ["external_order_no"])
    op.create_index("ix_procurements_line_id", "procurements", ["line_id"])


def downgrade() -> None:
    op.drop_index("ix_procurements_line_id", table_name="procurements")
    op.drop_index("ix_procurements_external_order_no", table_name="procurements")
    op.drop_index("ix_procurements_supplier_id", table_name="procurements")
    op.drop_index("ix_procurements_status", table_name="procurements")
    op.drop_index("ix_procurements_order_id", table_name="procurements")
    op.drop_table("procurements")
