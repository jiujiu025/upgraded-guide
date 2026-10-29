"""允许后台客户账号仅使用手机号联系"""

from alembic import op
import sqlalchemy as sa


revision = "0021_optional_customer_email"
down_revision = "0020_sales_login_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch_op:
        batch_op.alter_column("email", existing_type=sa.String(length=160), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    has_missing_email = connection.execute(
        sa.text("SELECT 1 FROM customers WHERE email IS NULL LIMIT 1")
    ).first()
    if has_missing_email:
        raise RuntimeError("仍有未填写邮箱的客户，请补全邮箱后再降级")
    with op.batch_alter_table("customers") as batch_op:
        batch_op.alter_column("email", existing_type=sa.String(length=160), nullable=False)
