"""runtime_settings：后台可改、立即生效的开关。

第一个用到的是「付款后自动采购」——它是真扣上游余额的动作，运营需要能随时掐断，
改环境变量重启 api 太重。
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_runtime_settings"
down_revision = "0032_procurements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_runtime_settings_key", "runtime_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_runtime_settings_key", table_name="runtime_settings")
    op.drop_table("runtime_settings")
