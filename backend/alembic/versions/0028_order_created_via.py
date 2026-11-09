"""订单增加创建来源与操作人

代客下单（docs/销售代客下单设计.md）需要在订单上直接看出这单是谁开的：
- created_via：self_service（客户自己下）/ sales_assisted（销售代客）/ admin（管理员代开）；
- created_by_user_id：具体操作人，用于按销售统计代客单量与排查异常单。

存量订单全部回填 self_service：它们都产生于代客下单上线之前，语义正确。
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_order_created_via"
down_revision = "0027_notification_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # created_by_user_id 刻意不加外键：审计字段，用户不会被硬删除；SQLite 也不支持
    # ALTER 加外键（批量重建要求历史匿名约束先命名，风险大于收益）。
    op.add_column("orders", sa.Column("created_via", sa.String(length=20), nullable=False, server_default="self_service"))
    op.add_column("orders", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_orders_created_by_user_id", "orders", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_created_by_user_id", table_name="orders")
    op.drop_column("orders", "created_by_user_id")
    op.drop_column("orders", "created_via")
