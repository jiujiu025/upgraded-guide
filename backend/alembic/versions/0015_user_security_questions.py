"""为用户增加密保问题哈希字段"""

from alembic import op
import sqlalchemy as sa


revision = "0015_user_security_questions"
down_revision = "0014_pg_unique_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("security_question_1", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("security_answer_hash_1", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("security_question_2", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("security_answer_hash_2", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "security_answer_hash_2")
    op.drop_column("users", "security_question_2")
    op.drop_column("users", "security_answer_hash_1")
    op.drop_column("users", "security_question_1")
