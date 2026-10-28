"""增加收件箱会话和消息"""
from alembic import op
import sqlalchemy as sa

revision = "0008_inbox"
down_revision = "0007_renewal_multi_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("assigned_sales_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("related_order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("related_line_id", sa.Integer(), sa.ForeignKey("customer_lines.id"), nullable=True),
        sa.Column("last_message_at", sa.DateTime(), nullable=False),
        sa.Column("customer_unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("staff_unread_count", sa.Integer(), nullable=False, server_default="0"),
    )
    for name, columns in [
        ("ix_inbox_threads_customer_id", ["customer_id"]),
        ("ix_inbox_threads_assigned_sales_id", ["assigned_sales_id"]),
        ("ix_inbox_threads_subject", ["subject"]),
        ("ix_inbox_threads_status", ["status"]),
        ("ix_inbox_threads_related_order_id", ["related_order_id"]),
        ("ix_inbox_threads_related_line_id", ["related_line_id"]),
        ("ix_inbox_threads_last_message_at", ["last_message_at"]),
    ]:
        op.create_index(name, "inbox_threads", columns)

    op.create_table(
        "inbox_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("inbox_threads.id"), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sender_role", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("customer_read_at", sa.DateTime(), nullable=True),
        sa.Column("staff_read_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inbox_messages_thread_id", "inbox_messages", ["thread_id"])
    op.create_index("ix_inbox_messages_sender_user_id", "inbox_messages", ["sender_user_id"])
    op.create_index("ix_inbox_messages_sender_role", "inbox_messages", ["sender_role"])


def downgrade() -> None:
    op.drop_table("inbox_messages")
    op.drop_table("inbox_threads")
