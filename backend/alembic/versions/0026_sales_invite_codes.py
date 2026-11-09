"""新增销售邀请码表

客户分配改用邀请码：走哪个销售的邀请码注册，客户就归谁。失效码不拒绝注册
（归公海 + 通知管理员），设计见 docs/销售邀请码客户归属设计.md。
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_sales_invite_codes"
down_revision = "0025_renewal_outreaches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_invite_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("sales_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.String(length=120), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sales_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["disabled_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # 模型里 code 是 unique=True + index=True → 单一唯一索引，与模型保持一致。
    op.create_index("ix_sales_invite_codes_code", "sales_invite_codes", ["code"], unique=True)
    op.create_index("ix_sales_invite_codes_sales_user_id", "sales_invite_codes", ["sales_user_id"])
    op.create_index("ix_sales_invite_codes_status", "sales_invite_codes", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sales_invite_codes_status", table_name="sales_invite_codes")
    op.drop_index("ix_sales_invite_codes_sales_user_id", table_name="sales_invite_codes")
    op.drop_index("ix_sales_invite_codes_code", table_name="sales_invite_codes")
    op.drop_table("sales_invite_codes")
