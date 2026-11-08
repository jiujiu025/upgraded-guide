"""新增支付通道验收记录表

线上收款从"写死关闭"改为"没有验收证据不许开"，本表存验收证据。
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_payment_acceptance"
down_revision = "0023_scheduled_task_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_channel_acceptances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("merchant_id", sa.String(length=80), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("channel_types_key", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("out_trade_no", sa.String(length=64), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payment_acceptance_amount_nonnegative"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_acceptance_scope",
        "payment_channel_acceptances",
        ["merchant_id", "secret_fingerprint", "status"],
    )
    op.create_index("ix_payment_channel_acceptances_verified_at", "payment_channel_acceptances", ["verified_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_channel_acceptances_verified_at", table_name="payment_channel_acceptances")
    op.drop_index("ix_payment_acceptance_scope", table_name="payment_channel_acceptances")
    op.drop_table("payment_channel_acceptances")
