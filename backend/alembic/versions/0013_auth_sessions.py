"""增加可撤销的轮换刷新会话"""

from alembic import op
import sqlalchemy as sa


revision = "0013_auth_sessions"
down_revision = "0012_catalog_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.String(40), nullable=True))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("auth_refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("auth_sessions.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_auth_refresh_tokens_session_id", "auth_refresh_tokens", ["session_id"])
    op.create_index("ix_auth_refresh_tokens_token_hash", "auth_refresh_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("auth_refresh_tokens")
    op.drop_table("auth_sessions")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "auth_version")
