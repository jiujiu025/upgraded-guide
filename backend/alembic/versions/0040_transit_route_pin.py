"""transit_routes 增加 pinned_vps_id：国家可以直接点名走哪台面板。

不填时保持原行为（组内按负载自动挑），所以对现有数据无影响。
外键用 SET NULL：面板被删后映射退回"组内自动"，不会把整条路由一起删掉。
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_transit_route_pin"
down_revision = "0039_transit_backups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transit_routes") as batch:
        batch.add_column(sa.Column("pinned_vps_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_transit_routes_pinned_vps", "transit_vps",
                                 ["pinned_vps_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("transit_routes") as batch:
        batch.drop_constraint("fk_transit_routes_pinned_vps", type_="foreignkey")
        batch.drop_column("pinned_vps_id")
