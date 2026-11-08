"""钱包账本巡检的检测能力验证：每种故障都必须被认出来，且不误报。"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Customer, PaymentOrder, PaymentRefund, User, WalletAccount, WalletTransaction,
)
from app.models.base import Base
from app.services.wallet_audit_service import audit_wallet_ledger
from app.services.wallet_service import apply_wallet_delta


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    user = User(username="audit_customer", password_hash="x", role="customer", display_name="客户甲")
    db.add(user)
    db.flush()
    customer = Customer(name="客户甲", user_id=user.id, balance=0)
    db.add(customer)
    db.commit()

    yield {"db": db, "user": user, "customer": customer}

    db.close()
    engine.dispose()


def _recharge(db, user, cents: int) -> PaymentOrder:
    payment = PaymentOrder(out_trade_no=f"OFF-{cents}", idempotency_key=f"rc-{cents}",
                           user_id=user.id, purpose="wallet_recharge", amount_cents=cents,
                           channel="offline", status="paid")
    db.add(payment)
    db.flush()
    apply_wallet_delta(db, user.id, cents, "offline_recharge", f"payment:{payment.id}",
                       payment_order_id=payment.id)
    db.commit()
    return payment


def _balance_payment(db, user, customer, cents: int) -> PaymentOrder:
    payment = PaymentOrder(out_trade_no=f"BAL-{cents}", idempotency_key=f"bal-{cents}",
                           user_id=user.id, purpose="order_payment", amount_cents=cents,
                           channel="balance", status="paid")
    db.add(payment)
    db.flush()
    apply_wallet_delta(db, user.id, -cents, "order_payment", f"balance-order:{payment.id}",
                       payment_order_id=payment.id)
    db.commit()
    return payment


def test_healthy_ledger_has_no_issue(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    _balance_payment(db, user, env["customer"], 6000)

    report = audit_wallet_ledger(db)

    assert report.ok
    assert report.issues == []
    assert report.accounts == 1
    assert report.transactions == 2


def test_detects_balance_diverged_from_ledger(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    account = db.query(WalletAccount).filter_by(user_id=user.id).one()
    account.balance_cents += 100        # 绕过流水直接加钱：账实不符
    db.commit()

    report = audit_wallet_ledger(db)

    assert not report.ok
    assert report.count("balance_vs_ledger") == 1
    assert report.stats["balance_vs_ledger"] == 1


def test_detects_broken_chain(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    account = db.query(WalletAccount).filter_by(user_id=user.id).one()
    db.add(WalletTransaction(wallet_account_id=account.id, transaction_type="order_payment",
                             amount_cents=-500, balance_before_cents=9900,   # 上条期末是 10000
                             balance_after_cents=9400, idempotency_key="broken-1"))
    account.balance_cents = 9500        # 余额仍等于流水累计，确保只触发"链不接"
    db.commit()

    report = audit_wallet_ledger(db)

    assert not report.ok
    assert report.count("ledger_chain") == 1
    chain = next(item for item in report.issues if item.kind == "ledger_chain")
    assert "不接" in chain.detail


def test_detects_transaction_internally_unbalanced(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    account = db.query(WalletAccount).filter_by(user_id=user.id).one()
    db.add(WalletTransaction(wallet_account_id=account.id, transaction_type="order_payment",
                             amount_cents=-500, balance_before_cents=10000,
                             balance_after_cents=9900, idempotency_key="unbalanced-1"))  # 应为 9500
    account.balance_cents = 9900
    db.commit()

    report = audit_wallet_ledger(db)

    assert not report.ok
    assert any("流水自身不平" in item.detail for item in report.issues)


def test_detects_paid_order_without_wallet_transaction(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    # 已标记收款，但钱包里没有任何流水
    db.add(PaymentOrder(out_trade_no="BAL-LOST", idempotency_key="bal-lost", user_id=user.id,
                        purpose="order_payment", amount_cents=6000, channel="balance", status="paid"))
    db.commit()

    report = audit_wallet_ledger(db)

    assert not report.ok
    assert report.count("missing_wallet_transaction") == 1
    assert "BAL-LOST" in next(item for item in report.issues
                              if item.kind == "missing_wallet_transaction").detail


def test_online_order_payment_is_not_expected_in_wallet(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    db.add(PaymentOrder(out_trade_no="ONLINE-1", idempotency_key="online-1", user_id=user.id,
                        purpose="order_payment", amount_cents=6000, channel="online", status="paid"))
    db.commit()

    report = audit_wallet_ledger(db)

    assert report.ok, [item.detail for item in report.issues]


def test_refunded_balance_order_is_not_flagged(env):
    """同一张支付单上既有扣款又有退回，净额为 0；不能因此判定"扣款丢失"。"""
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    payment = _balance_payment(db, user, env["customer"], 6000)
    apply_wallet_delta(db, user.id, 6000, "order_refund", f"refund:{payment.id}",
                       payment_order_id=payment.id)
    payment.status = "refunded"
    db.commit()

    report = audit_wallet_ledger(db)

    assert report.ok, [item.detail for item in report.issues]
    assert report.count("missing_wallet_transaction") == 0


def test_detects_missing_refund_transaction(env):
    db, user = env["db"], env["user"]
    _recharge(db, user, 10000)
    payment = _balance_payment(db, user, env["customer"], 6000)
    db.add(PaymentRefund(payment_order_id=payment.id, amount_cents=6000, channel="balance",
                         reason="测试退款未记账", actor_user_id=user.id, status="completed"))
    db.commit()

    report = audit_wallet_ledger(db)

    assert not report.ok
    assert report.count("missing_refund_transaction") == 1


def test_detects_customer_float_copy_drift(env):
    db, user, customer = env["db"], env["user"], env["customer"]
    _recharge(db, user, 10000)
    customer.balance = 99.99        # 冗余副本被别处改坏
    db.commit()

    report = audit_wallet_ledger(db)

    # 浮点副本分叉不是账错，但必须报出来
    assert report.ok
    assert report.count("customer_float_drift") == 1
    assert report.stats["customer_float_drift"] == 1


def test_detects_orphan_customer_balance(env):
    db, customer = env["db"], env["customer"]
    customer.user_id = None          # 无账号，自然也没有钱包
    customer.balance = 50
    db.commit()

    report = audit_wallet_ledger(db)

    assert report.count("orphan_customer_balance") == 1
    assert report.stats["orphan_customer_balance"] == 1


def test_detects_negative_balance(env):
    """负余额被 ck_wallet_balance_nonnegative 挡着，正常写不进来；
    这里关掉 SQLite 的约束检查，模拟"旧库没有该约束 / 有人绕过应用直接改库"的情况。"""
    db, user = env["db"], env["user"]
    account = WalletAccount(user_id=user.id, balance_cents=0, currency="CNY")
    db.add(account)
    db.commit()
    db.execute(text("PRAGMA ignore_check_constraints=ON"))
    db.execute(text("UPDATE wallet_accounts SET balance_cents = -1 WHERE id = :id"), {"id": account.id})
    db.commit()
    db.expire_all()

    report = audit_wallet_ledger(db)

    assert report.count("negative_balance") == 1
