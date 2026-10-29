"""记录销售账号登录事件"""

from alembic import op
import sqlalchemy as sa


revision = "0020_sales_login_events"
down_revision = "0019_retail_price_book"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_login_events",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("failure_reason", sa.String(length=40), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_sales_login_events_user_created", "sales_login_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_sales_login_events_user_created", table_name="sales_login_events")
    op.drop_table("sales_login_events")
