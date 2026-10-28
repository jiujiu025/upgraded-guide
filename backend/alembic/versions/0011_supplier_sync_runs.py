"""新增供应商同步运行记录"""

from alembic import op
import sqlalchemy as sa


revision = "0011_supplier_sync_runs"
down_revision = "0010_supplier_readonly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sync_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.Column("items_changed", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ["supplier_id", "actor_id", "sync_type", "status", "started_at"]:
        op.create_index(f"ix_supplier_sync_runs_{column}", "supplier_sync_runs", [column])


def downgrade() -> None:
    op.drop_table("supplier_sync_runs")
