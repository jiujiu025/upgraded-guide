"""audit_logs.actor_id 允许为空：系统动作也要留痕。

支付回调、定时任务、自动采购没有登录用户，之前 NOT NULL 只能整条不写，
结果最该回溯的动作反而没有记录。
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_audit_actor_nullable"
down_revision = "0033_runtime_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("audit_logs", "actor_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # 回滚前必须清掉没有 actor 的记录，否则约束加不回去
    op.execute("DELETE FROM audit_logs WHERE actor_id IS NULL")
    op.alter_column("audit_logs", "actor_id", existing_type=sa.Integer(), nullable=False)
