"""system_logs：系统运行日志。

与 audit_logs 分工不同：审计记"人做了什么"，这里记"系统干活时哪一步出了问题"。
保留 30 天，由定时任务清理。
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_system_logs"
down_revision = "0037_impersonation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("vps_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_system_logs_level", "system_logs", ["level"])
    op.create_index("ix_system_logs_category", "system_logs", ["category"])
    op.create_index("ix_system_logs_action", "system_logs", ["action"])
    op.create_index("ix_system_logs_actor_id", "system_logs", ["actor_id"])
    op.create_index("ix_system_logs_order_id", "system_logs", ["order_id"])
    op.create_index("ix_system_logs_created_at", "system_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_system_logs_created_at", table_name="system_logs")
    op.drop_index("ix_system_logs_order_id", table_name="system_logs")
    op.drop_index("ix_system_logs_actor_id", table_name="system_logs")
    op.drop_index("ix_system_logs_action", table_name="system_logs")
    op.drop_index("ix_system_logs_category", table_name="system_logs")
    op.drop_index("ix_system_logs_level", table_name="system_logs")
    op.drop_table("system_logs")
