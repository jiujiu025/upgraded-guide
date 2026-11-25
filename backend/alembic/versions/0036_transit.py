"""中转自动化：transit_vps / transit_routes / transit_provisions。

三张表各管的是一件事：
- transit_vps：承接中转的 VPS（3x-ui 面板）。按区域组（us / hk）归类，
  不按国家一一对应——北美欧洲走美国机，东南亚南非走香港机是业务现状。
- transit_routes：国家 → 区域组。单独建表是为了新增国家时改配置，不用改代码。
- transit_provisions：某订单第 N 条线路建了哪个入站/出站/路由。
  幂等键 (order_id, line_index)，重试时据此跳过已完成的步骤。

为什么不是往 procurements 加字段：采购与中转是两次独立的扣钱/改动动作，
采购成功但中转失败时要能单独重试，混在一行里没法区分状态。
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_transit"
down_revision = "0035_order_previous_product"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transit_vps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("group_code", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=True),
        sa.Column("encrypted_password", sa.String(length=500), nullable=True),
        sa.Column("encrypted_api_token", sa.String(length=500), nullable=True),
        sa.Column("connect_host", sa.String(length=120), nullable=False),
        sa.Column("reality_private_key", sa.String(length=120), nullable=True),
        sa.Column("reality_public_key", sa.String(length=120), nullable=True),
        sa.Column("reality_short_id", sa.String(length=32), nullable=True),
        sa.Column("sni", sa.String(length=120), nullable=False, server_default="yahoo.com"),
        sa.Column("dest", sa.String(length=160), nullable=False, server_default="yahoo.com:443"),
        sa.Column("fingerprint", sa.String(length=20), nullable=False, server_default="random"),
        sa.Column("port_start", sa.Integer(), nullable=False, server_default="52000"),
        sa.Column("port_end", sa.Integer(), nullable=False, server_default="52999"),
        sa.Column("max_inbounds", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_transit_vps_name"),
    )
    op.create_index("ix_transit_vps_group_code", "transit_vps", ["group_code"])
    op.create_index("ix_transit_vps_is_enabled", "transit_vps", ["is_enabled"])

    op.create_table(
        "transit_routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_code", sa.String(length=10), nullable=False),
        sa.Column("group_code", sa.String(length=20), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("country_code", name="uq_transit_route_country"),
    )
    op.create_index("ix_transit_routes_country_code", "transit_routes", ["country_code"])
    op.create_index("ix_transit_routes_group_code", "transit_routes", ["group_code"])

    op.create_table(
        "transit_provisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("transit_vps_id", sa.Integer(), sa.ForeignKey("transit_vps.id"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("inbound_id", sa.Integer(), nullable=True),
        sa.Column("inbound_port", sa.Integer(), nullable=True),
        sa.Column("uuid", sa.String(length=64), nullable=True),
        sa.Column("outbound_tag", sa.String(length=80), nullable=True),
        sa.Column("exit_ip", sa.String(length=64), nullable=True),
        sa.Column("encrypted_exit_credentials", sa.Text(), nullable=True),
        sa.Column("remark", sa.String(length=160), nullable=True),
        sa.Column("node_link", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "line_index", name="uq_transit_provision_order_line"),
    )
    op.create_index("ix_transit_provisions_order_id", "transit_provisions", ["order_id"])
    op.create_index("ix_transit_provisions_status", "transit_provisions", ["status"])
    op.create_index("ix_transit_provisions_transit_vps_id", "transit_provisions", ["transit_vps_id"])
    op.create_index("ix_transit_provisions_line_id", "transit_provisions", ["line_id"])


def downgrade() -> None:
    op.drop_index("ix_transit_provisions_line_id", table_name="transit_provisions")
    op.drop_index("ix_transit_provisions_transit_vps_id", table_name="transit_provisions")
    op.drop_index("ix_transit_provisions_status", table_name="transit_provisions")
    op.drop_index("ix_transit_provisions_order_id", table_name="transit_provisions")
    op.drop_table("transit_provisions")

    op.drop_index("ix_transit_routes_group_code", table_name="transit_routes")
    op.drop_index("ix_transit_routes_country_code", table_name="transit_routes")
    op.drop_table("transit_routes")

    op.drop_index("ix_transit_vps_is_enabled", table_name="transit_vps")
    op.drop_index("ix_transit_vps_group_code", table_name="transit_vps")
    op.drop_table("transit_vps")
