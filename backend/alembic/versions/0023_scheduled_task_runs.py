"""新增定时任务执行流水表"""

from alembic import op
import sqlalchemy as sa


revision = "0023_scheduled_task_runs"
down_revision = "0022_product_resource_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_task_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("triggered_by", sa.String(length=20), nullable=False, server_default="scheduler"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_task_runs_task_name", "scheduled_task_runs", ["task_name"])
    op.create_index("ix_scheduled_task_runs_status", "scheduled_task_runs", ["status"])
    op.create_index("ix_scheduled_task_runs_started_at", "scheduled_task_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_task_runs_started_at", table_name="scheduled_task_runs")
    op.drop_index("ix_scheduled_task_runs_status", table_name="scheduled_task_runs")
    op.drop_index("ix_scheduled_task_runs_task_name", table_name="scheduled_task_runs")
    op.drop_table("scheduled_task_runs")
