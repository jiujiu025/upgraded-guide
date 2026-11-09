"""新增通知渠道分发三张表

站内信照旧写 notifications；本迁移加"往哪推"的配置与投递流水：
- notification_channels：渠道（企微应用/群机器人，后续 TG），config 加密落库；
- notification_channel_bindings：用户 ↔ 推送目标（企微 userid / TG chat_id）；
- notification_deliveries：投递流水，(通知, 渠道, 目标) 唯一约束做幂等。
设计见 docs/通知渠道分发设计.md。
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_notification_channels"
down_revision = "0026_sales_invite_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_type", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("config_encrypted", sa.Text(), nullable=False),
        sa.Column("push_categories", sa.String(length=200), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notification_channel_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(length=120), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel_id", name="uq_notification_binding_user_channel"),
    )
    op.create_index("ix_notification_channel_bindings_user_id", "notification_channel_bindings", ["user_id"])
    op.create_index("ix_notification_channel_bindings_channel_id", "notification_channel_bindings", ["channel_id"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "channel_id", "target", name="uq_notification_delivery_target"),
    )
    op.create_index("ix_notification_deliveries_notification_id", "notification_deliveries", ["notification_id"])
    op.create_index("ix_notification_deliveries_channel_id", "notification_deliveries", ["channel_id"])
    op.create_index("ix_notification_deliveries_target", "notification_deliveries", ["target"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_target", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_channel_id", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_notification_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_channel_bindings_channel_id", table_name="notification_channel_bindings")
    op.drop_index("ix_notification_channel_bindings_user_id", table_name="notification_channel_bindings")
    op.drop_table("notification_channel_bindings")
    op.drop_table("notification_channels")
