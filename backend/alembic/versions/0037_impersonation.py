"""impersonation_sessions：超级管理员模拟销售/客户登录的会话记录。

与 AuthSession 完全分开：模拟必须能立刻吊销、10 分钟自动失效，且审计上要能
区分"管理员在操作"和"本人操作"。混进真实会话会让退出模拟变成退出登录。
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_impersonation"
down_revision = "0036_transit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "impersonation_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("end_reason", sa.String(length=30), nullable=True),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_impersonation_sessions_actor_id", "impersonation_sessions", ["actor_id"])
    op.create_index("ix_impersonation_sessions_target_user_id", "impersonation_sessions", ["target_user_id"])
    op.create_index("ix_impersonation_sessions_token_hash", "impersonation_sessions", ["token_hash"], unique=True)
    op.create_index("ix_impersonation_sessions_expires_at", "impersonation_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_impersonation_sessions_expires_at", table_name="impersonation_sessions")
    op.drop_index("ix_impersonation_sessions_token_hash", table_name="impersonation_sessions")
    op.drop_index("ix_impersonation_sessions_target_user_id", table_name="impersonation_sessions")
    op.drop_index("ix_impersonation_sessions_actor_id", table_name="impersonation_sessions")
    op.drop_table("impersonation_sessions")
