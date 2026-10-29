"""增加钱包账本、支付订单与销售佣金记录"""

from datetime import datetime
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = "0018_finance_ledger"
down_revision = "0017_resource_segments_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=40), nullable=True))
    op.create_index("ix_users_phone", "users", ["phone"])
    op.create_table(
        "wallet_accounts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("balance_cents >= 0", name="ck_wallet_balance_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_wallet_accounts_user_id", "wallet_accounts", ["user_id"])

    op.create_table(
        "payment_channel_configs",
        sa.Column("merchant_id", sa.String(length=80), nullable=True),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("channel_types", sa.JSON(), nullable=False),
        sa.Column("min_amount_cents", sa.Integer(), server_default="100", nullable=False),
        sa.Column("max_amount_cents", sa.Integer(), server_default="10000000", nullable=False),
        sa.Column("online_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "payment_orders",
        sa.Column("out_trade_no", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("sales_id_snapshot", sa.Integer(), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("provider_type", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("platform_trade_no", sa.String(length=100), nullable=True),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_payment_order_amount_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["sales_id_snapshot"], ["users.id"]),
        sa.UniqueConstraint("out_trade_no", name="uq_payment_order_out_trade_no"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_order_idempotency_key"),
    )
    for column in ("user_id", "purpose", "order_id", "sales_id_snapshot", "status", "platform_trade_no", "paid_at"):
        op.create_index(f"ix_payment_orders_{column}", "payment_orders", [column])
    op.create_index("ix_payment_orders_out_trade_no", "payment_orders", ["out_trade_no"])

    op.create_table(
        "wallet_transactions",
        sa.Column("wallet_account_id", sa.Integer(), nullable=False),
        sa.Column("transaction_type", sa.String(length=30), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_before_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_cents", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("payment_order_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("remark", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_cents != 0", name="ck_wallet_transaction_amount_nonzero"),
        sa.CheckConstraint("balance_before_cents >= 0 AND balance_after_cents >= 0", name="ck_wallet_transaction_balances_nonnegative"),
        sa.ForeignKeyConstraint(["wallet_account_id"], ["wallet_accounts.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["payment_order_id"], ["payment_orders.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_wallet_transaction_idempotency_key"),
    )
    for column in ("wallet_account_id", "transaction_type", "actor_user_id", "payment_order_id", "order_id"):
        op.create_index(f"ix_wallet_transactions_{column}", "wallet_transactions", [column])

    op.create_table(
        "payment_refunds",
        sa.Column("payment_order_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("external_reference", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="completed", nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["payment_order_id"], ["payment_orders.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.UniqueConstraint("payment_order_id"),
    )
    op.create_index("ix_payment_refunds_payment_order_id", "payment_refunds", ["payment_order_id"])
    op.create_index("ix_payment_refunds_actor_user_id", "payment_refunds", ["actor_user_id"])

    op.create_table(
        "sales_commission_entries",
        sa.Column("sales_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("payment_order_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("basis_cents", sa.BigInteger(), nullable=False),
        sa.Column("rate_basis_points", sa.Integer(), server_default="1500", nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sales_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["payment_order_id"], ["payment_orders.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_commission_idempotency_key"),
    )
    for column in ("sales_id", "customer_id", "order_id", "payment_order_id", "actor_user_id"):
        op.create_index(f"ix_sales_commission_entries_{column}", "sales_commission_entries", [column])

    now = datetime.utcnow()
    connection = op.get_bind()
    customers = connection.execute(sa.text(
        "SELECT user_id, balance FROM customers WHERE user_id IS NOT NULL AND is_deleted IS FALSE"
    )).all()
    sales_users = connection.execute(sa.text(
        "SELECT id FROM users WHERE role = 'sales' AND is_active IS TRUE"
    )).all()
    account_ids: dict[int, int] = {}
    next_account_id = 1
    for row in customers:
        amount = Decimal(str(row.balance or 0)) * 100
        if amount != amount.to_integral_value() or amount < 0:
            raise RuntimeError("客户旧余额存在负数或不足整分金额；请先人工盘点修正，再执行财务迁移")
        user_id = int(row.user_id)
        account_ids[user_id] = next_account_id
        connection.execute(sa.text(
            "INSERT INTO wallet_accounts (id, user_id, balance_cents, currency, created_at, updated_at) "
            "VALUES (:id, :user_id, :balance, 'CNY', :created_at, :updated_at)"
        ), {"id": next_account_id, "user_id": user_id, "balance": int(amount), "created_at": now, "updated_at": now})
        next_account_id += 1
    for row in sales_users:
        user_id = int(row.id)
        if user_id in account_ids:
            continue
        account_ids[user_id] = next_account_id
        connection.execute(sa.text(
            "INSERT INTO wallet_accounts (id, user_id, balance_cents, currency, created_at, updated_at) "
            "VALUES (:id, :user_id, 0, 'CNY', :created_at, :updated_at)"
        ), {"id": next_account_id, "user_id": user_id, "created_at": now, "updated_at": now})
        next_account_id += 1

    opening_id = 1
    for row in customers:
        amount = int(Decimal(str(row.balance or 0)) * 100)
        if amount == 0:
            continue
        account_id = account_ids[int(row.user_id)]
        connection.execute(sa.text(
            "INSERT INTO wallet_transactions (id, wallet_account_id, transaction_type, amount_cents, "
            "balance_before_cents, balance_after_cents, idempotency_key, remark, created_at, updated_at) "
            "VALUES (:id, :account_id, 'opening_balance', :amount, 0, :amount, :idem, :remark, :created_at, :updated_at)"
        ), {"id": opening_id, "account_id": account_id, "amount": amount,
            "idem": f"opening:{int(row.user_id)}", "remark": "旧客户余额迁移期初流水",
            "created_at": now, "updated_at": now})
        opening_id += 1

    connection.execute(sa.text(
        "INSERT INTO payment_channel_configs (id, channel_types, min_amount_cents, max_amount_cents, online_enabled, created_at, updated_at) "
        "VALUES (1, '[]', 100, 10000000, :enabled, :created_at, :updated_at)"
    ), {"enabled": False, "created_at": now, "updated_at": now})


def downgrade() -> None:
    for column in ("actor_user_id", "payment_order_id", "order_id", "customer_id", "sales_id"):
        op.drop_index(f"ix_sales_commission_entries_{column}", table_name="sales_commission_entries")
    op.drop_table("sales_commission_entries")
    op.drop_index("ix_payment_refunds_actor_user_id", table_name="payment_refunds")
    op.drop_index("ix_payment_refunds_payment_order_id", table_name="payment_refunds")
    op.drop_table("payment_refunds")
    for column in ("order_id", "payment_order_id", "actor_user_id", "transaction_type", "wallet_account_id"):
        op.drop_index(f"ix_wallet_transactions_{column}", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    for column in ("paid_at", "platform_trade_no", "status", "sales_id_snapshot", "order_id", "purpose", "user_id"):
        op.drop_index(f"ix_payment_orders_{column}", table_name="payment_orders")
    op.drop_index("ix_payment_orders_out_trade_no", table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_table("payment_channel_configs")
    op.drop_index("ix_wallet_accounts_user_id", table_name="wallet_accounts")
    op.drop_table("wallet_accounts")
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_column("users", "phone")
