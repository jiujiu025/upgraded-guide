"""为供应商增加加密凭据字段"""

from alembic import op
import sqlalchemy as sa


revision = "0016_supplier_credential_ciphertext"
down_revision = "0015_user_security_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("encrypted_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("suppliers", "encrypted_api_key")
