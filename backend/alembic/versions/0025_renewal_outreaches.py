"""新增到期触达留痕表

客户到期提醒原来只有站内信，客户不登录等于没提醒。实际催续费发生在微信里，
本表记录销售做过的那次触达：渠道、文案快照、结果、当时剩余天数。
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_renewal_outreaches"
down_revision = "0024_payment_acceptance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "renewal_outreaches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("sales_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("days_left", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["line_id"], ["customer_lines.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["sales_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_renewal_outreaches_line_id", "renewal_outreaches", ["line_id"])
    op.create_index("ix_renewal_outreaches_customer_id", "renewal_outreaches", ["customer_id"])
    op.create_index("ix_renewal_outreaches_sales_id", "renewal_outreaches", ["sales_id"])
    op.create_index("ix_renewal_outreaches_outcome", "renewal_outreaches", ["outcome"])
    op.create_index("ix_renewal_outreaches_line_created", "renewal_outreaches", ["line_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_renewal_outreaches_line_created", table_name="renewal_outreaches")
    op.drop_index("ix_renewal_outreaches_outcome", table_name="renewal_outreaches")
    op.drop_index("ix_renewal_outreaches_sales_id", table_name="renewal_outreaches")
    op.drop_index("ix_renewal_outreaches_customer_id", table_name="renewal_outreaches")
    op.drop_index("ix_renewal_outreaches_line_id", table_name="renewal_outreaches")
    op.drop_table("renewal_outreaches")
