"""transit_backups：3x-ui 面板配置备份。

备份的是"能原样重建"的东西：每条入站的完整定义（含 UUID、reality 私钥、shortId、
端口）+ Xray 模板（出站与路由）。恢复时端口和密钥保持不变，客户链接就不用重发，
换机器只需要把域名解析指到新 IP。
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_transit_backups"
down_revision = "0038_system_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transit_backups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vps_id", sa.Integer(), sa.ForeignKey("transit_vps.id"), nullable=True),
        sa.Column("vps_name", sa.String(length=60), nullable=False),
        sa.Column("filename", sa.String(length=160), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("inbound_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outbound_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rule_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ready"),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("restored_at", sa.DateTime(), nullable=True),
        sa.Column("restored_to_vps_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_transit_backups_vps_id", "transit_backups", ["vps_id"])
    op.create_index("ix_transit_backups_status", "transit_backups", ["status"])


def downgrade() -> None:
    op.drop_index("ix_transit_backups_status", table_name="transit_backups")
    op.drop_index("ix_transit_backups_vps_id", table_name="transit_backups")
    op.drop_table("transit_backups")
