"""给商品增加资源分类，用于按零售价目表的月价线性定价"""

from alembic import op
import sqlalchemy as sa


revision = "0022_product_resource_category"
down_revision = "0021_optional_customer_email"
branch_labels = None
depends_on = None


# 迁移是历史快照：标签写死在这里，不引用 app.services.*（未来改文案不应让老迁移失效）。
CATEGORY_LABELS = {
    "独享静态住宅（普通）": "static_residential",
    "独享静态住宅（本土原生）": "static_residential_native",
    "独享静态住宅（运营商原生）": "static_residential_isp",
    "独享静态数据中心": "static_datacenter",
}


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("resource_category", sa.String(length=50), nullable=True))
        batch_op.create_index("ix_products_resource_category", ["resource_category"])
    connection = op.get_bind()
    for label, code in CATEGORY_LABELS.items():
        connection.execute(
            sa.text(
                "UPDATE products SET resource_category = :code "
                "WHERE resource_category IS NULL AND product_type = :label"
            ),
            {"code": code, "label": label},
        )


def downgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_index("ix_products_resource_category")
        batch_op.drop_column("resource_category")
