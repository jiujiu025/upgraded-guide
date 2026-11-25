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
    # 走 batch：SQLite 不支持 ALTER COLUMN，直接 op.alter_column 会生成
    # "ALTER TABLE ... ALTER COLUMN ..." 被旧版 SQLite 拒掉（CI 的空库迁移就是这么挂的）。
    # batch 在 PostgreSQL 上是直通 ALTER，只有 SQLite 才重建表。
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column("actor_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # 回滚前必须清掉没有 actor 的记录，否则约束加不回去
    op.execute("DELETE FROM audit_logs WHERE actor_id IS NULL")
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column("actor_id", existing_type=sa.Integer(), nullable=False)
