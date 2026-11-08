from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Customer, CustomerLine, Notification, Order, PaymentOrder, Product, Region,
    ScheduledTaskRun, User,
)
from app.models.base import Base
from app.tasks import lines, orders, payments, registry, wallet
from app.tasks.runner import execute

NOW = datetime(2026, 11, 8, 12, 0, 0)


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # 与生产 SessionLocal 保持一致：autoflush=False，避免查询触发半成品写入。
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = Session()
    region = Region(name="美国", country_code="US")
    product = Product(name="美国静态住宅", product_type="独享静态住宅（普通）", price=60, billing_days=30, region_id=1)
    customer_user = User(username="task_customer", password_hash="x", role="customer", display_name="客户甲")
    admin = User(username="task_admin", password_hash="x", role="admin", display_name="管理员")
    sales = User(username="task_sales", password_hash="x", role="sales", display_name="销售甲")
    db.add_all([region, customer_user, admin, sales])
    db.flush()
    product.region_id = region.id
    customer = Customer(name="客户甲", user_id=customer_user.id, sales_id=sales.id)
    db.add_all([product, customer])
    db.flush()
    db.commit()
    yield db, Session
    db.close()
    engine.dispose()


def _order(db, customer, product, *, status="pending_payment", created_at=NOW - timedelta(days=2), payment_status="unpaid"):
    order = Order(
        order_no=f"ORD-{uuid4().hex[:12].upper()}",
        customer_id=customer.id,
        product_id=product.id,
        amount=60,
        total_amount=60,
        unit_price=60,
        status=status,
        payment_status=payment_status,
        created_at=created_at,
        product_name=product.name,
    )
    db.add(order)
    db.flush()
    return order


def test_close_expired_orders_closes_unpaid_and_skips_active_payment(env):
    db, _ = env
    customer = db.scalar(select(Customer))
    product = db.scalar(select(Product))

    expired = _order(db, customer, product)
    fresh = _order(db, customer, product, created_at=NOW - timedelta(minutes=30))
    paying = _order(db, customer, product, created_at=NOW - timedelta(days=3))
    db.add(PaymentOrder(
        out_trade_no="PAY-PENDING",
        idempotency_key="idem-pending",
        user_id=customer.user_id,
        purpose="order_payment",
        order_id=paying.id,
        amount_cents=6000,
        channel="wechat",
        status="pending",
    ))
    contact = _order(db, customer, product, status="pending_confirm", created_at=NOW - timedelta(days=5))
    db.commit()

    counts = orders.close_expired_orders(db, now=NOW, timeout_minutes=1440)
    db.commit()

    assert counts["closed"] == 1
    assert counts["skipped_active_payment"] == 1
    assert db.get(Order, expired.id).status == "cancelled"
    assert db.get(Order, expired.id).cancelled_at == NOW
    assert db.get(Order, fresh.id).status == "pending_payment"
    assert db.get(Order, paying.id).status == "pending_payment"
    assert db.get(Order, contact.id).status == "pending_confirm"

    notices = db.scalars(select(Notification).where(Notification.user_id == customer.user_id)).all()
    assert [notice.title for notice in notices] == ["订单已自动关闭"]


def test_line_reminders_are_bucketed_and_idempotent(env):
    db, _ = env
    customer = db.scalar(select(Customer))
    product = db.scalar(select(Product))
    soon = CustomerLine(customer_id=customer.id, node_id=1, product_id=product.id,
                        started_at=NOW - timedelta(days=23), expires_at=NOW + timedelta(days=6, hours=12))
    near = CustomerLine(customer_id=customer.id, node_id=1, product_id=product.id,
                        started_at=NOW - timedelta(days=27), expires_at=NOW + timedelta(days=2, hours=5))
    far = CustomerLine(customer_id=customer.id, node_id=1, product_id=product.id,
                       started_at=NOW, expires_at=NOW + timedelta(days=30))
    db.add_all([soon, near, far])
    db.commit()

    first = lines.line_expiry_reminders(db, now=NOW, reminder_days=(7, 3, 1))
    db.commit()
    assert first["sent"] == 2
    assert first["thresholds"] == [1, 3, 7]

    second = lines.line_expiry_reminders(db, now=NOW, reminder_days=(7, 3, 1))
    db.commit()
    assert second["sent"] == 0
    assert second["already_sent"] == 2

    contents = db.scalars(select(Notification.content).where(Notification.category == "expiration")).all()
    assert any(f"线路 {soon.id} 即将到期（剩余 7 天提醒）" in item for item in contents)
    assert any(f"线路 {near.id} 即将到期（剩余 3 天提醒）" in item for item in contents)


def test_line_reminders_respect_legacy_lazy_notification(env):
    db, _ = env
    customer = db.scalar(select(Customer))
    product = db.scalar(select(Product))
    line = CustomerLine(customer_id=customer.id, node_id=1, product_id=product.id,
                        started_at=NOW - timedelta(days=24), expires_at=NOW + timedelta(days=6))
    db.add(line)
    db.flush()
    db.add(Notification(user_id=customer.user_id, title="线路即将到期",
                        content=f"线路 {line.id} 即将到期，请及时查看续费选项。", category="expiration"))
    db.commit()

    counts = lines.line_expiry_reminders(db, now=NOW, reminder_days=(7, 3, 1))
    db.commit()
    assert counts["sent"] == 0
    assert counts["already_sent"] == 1


def test_payment_reconcile_expires_stale_and_reports_differences(env):
    db, _ = env
    customer = db.scalar(select(Customer))
    admin = db.scalar(select(User).where(User.role == "admin"))
    product = db.scalar(select(Product))
    order = _order(db, customer, product)

    stale = PaymentOrder(out_trade_no="PAY-STALE", idempotency_key="idem-stale", user_id=customer.user_id,
                         purpose="wallet_recharge", amount_cents=10000, channel="wechat",
                         status="pending", expires_at=NOW - timedelta(minutes=30))
    no_ledger = PaymentOrder(out_trade_no="PAY-PAID", idempotency_key="idem-paid", user_id=customer.user_id,
                             purpose="wallet_recharge", amount_cents=20000, channel="wechat",
                             status="paid", paid_at=NOW - timedelta(hours=1))
    order_paid = PaymentOrder(out_trade_no="PAY-ORDER", idempotency_key="idem-order", user_id=customer.user_id,
                              purpose="order_payment", order_id=order.id, amount_cents=6000,
                              channel="wechat", status="paid", paid_at=NOW - timedelta(hours=2))
    review = PaymentOrder(out_trade_no="PAY-REVIEW", idempotency_key="idem-review", user_id=customer.user_id,
                          purpose="wallet_recharge", amount_cents=30000, channel="wechat",
                          status="manual_review", created_at=NOW - timedelta(days=2))
    db.add_all([stale, no_ledger, order_paid, review])
    db.commit()

    counts = payments.reconcile_payments(db, now=NOW, grace_minutes=5)
    db.commit()

    assert counts["expired_payment_orders"] == 1
    assert counts["missing_wallet_credit"] == 1
    assert counts["order_not_advanced"] == 1
    assert counts["manual_review_backlog"] == 1
    assert counts["admins_notified"] == 1
    assert db.get(PaymentOrder, stale.id).status == "expired"

    again = payments.reconcile_payments(db, now=NOW + timedelta(minutes=15), grace_minutes=5)
    db.commit()
    assert again["admins_notified"] == 0
    finance_notices = db.scalars(
        select(Notification).where(Notification.user_id == admin.id, Notification.category == "finance")
    ).all()
    assert len(finance_notices) == 1


def test_reconcile_ignores_orders_advanced_to_paid(env):
    db, _ = env
    customer = db.scalar(select(Customer))
    product = db.scalar(select(Product))
    order = _order(db, customer, product, status="paid", payment_status="paid")
    db.add(PaymentOrder(out_trade_no="PAY-OK", idempotency_key="idem-ok", user_id=customer.user_id,
                        purpose="order_payment", order_id=order.id, amount_cents=6000,
                        channel="wechat", status="paid", paid_at=NOW))
    db.commit()

    counts = payments.reconcile_payments(db, now=NOW, grace_minutes=5)
    db.commit()
    assert counts["order_not_advanced"] == 0
    assert counts["admins_notified"] == 0


def test_task_runs_requires_authentication():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/admin/task-runs").status_code == 401
    assert client.get("/api/admin/task-runs/catalog").status_code == 401
    assert client.post("/api/admin/task-runs/close_expired_orders/run").status_code == 401


def test_execute_records_run_and_survives_failure(env, monkeypatch):
    db, Session = env
    customer = db.scalar(select(Customer))
    product = db.scalar(select(Product))
    _order(db, customer, product)
    db.commit()

    monkeypatch.setattr("app.tasks.runner.SessionLocal", Session)

    outcome = execute("close_expired_orders", triggered_by="test", now=NOW)
    assert outcome.status == "success"
    assert outcome.counts["closed"] == 1

    run = db.scalar(select(ScheduledTaskRun).order_by(ScheduledTaskRun.id.desc()))
    db.refresh(run)
    assert run.task_name == "close_expired_orders"
    assert run.status == "success"
    assert run.triggered_by == "test"
    assert '"closed": 1' in run.summary

    missing = execute("not_a_task", triggered_by="test", now=NOW)
    assert missing.status == "failed"
    assert missing.error


def test_execute_records_failure_when_handler_raises(env, monkeypatch):
    db, Session = env
    monkeypatch.setattr("app.tasks.runner.SessionLocal", Session)

    def boom(_db, _now):
        raise RuntimeError("上游接口不可用")

    monkeypatch.setitem(
        registry.TASKS,
        "close_expired_orders",
        registry.TaskSpec("close_expired_orders", "关闭超时未支付订单", 30, boom),
    )

    outcome = execute("close_expired_orders", triggered_by="test", now=NOW)
    assert outcome.status == "failed"
    assert "上游接口不可用" in outcome.error

    run = db.scalar(select(ScheduledTaskRun).order_by(ScheduledTaskRun.id.desc()))
    db.refresh(run)
    assert run.status == "failed"
    assert "上游接口不可用" in run.error


# --- 钱包账本巡检任务 -------------------------------------------------------

def _break_wallet_balance(db, user_id: int) -> None:
    """造一个阻断项：账户余额与流水累计不符（模拟绕过应用改库）。"""
    from app.models import WalletAccount
    from app.services.wallet_service import apply_wallet_delta

    apply_wallet_delta(db, user_id, 10000, "offline_recharge", "audit-task-1")
    account = db.scalar(select(WalletAccount).where(WalletAccount.user_id == user_id))
    account.balance_cents = 9000
    db.commit()


def test_wallet_audit_is_silent_when_ledger_is_clean(env):
    db, _ = env
    result = wallet.audit_wallet(db, now=NOW)
    assert result["blocking"] == 0
    # 提示项（这里没有）也不该推通知，否则过渡期天天报警
    assert result["admins_notified"] == 0
    assert db.scalars(select(Notification)).all() == []


def test_wallet_audit_notifies_once_per_day_while_broken(env):
    db, _ = env
    user_id = db.scalar(select(User.id).where(User.username == "task_customer"))
    _break_wallet_balance(db, user_id)

    first = wallet.audit_wallet(db, now=NOW)
    assert first["blocking"] == 1
    assert first["balance_vs_ledger"] == 1
    assert first["admins_notified"] == 1
    notification = db.scalar(select(Notification).where(Notification.category == "finance"))
    assert "钱包账本巡检发现阻断问题" == notification.title
    assert "余额" in notification.content

    # 同一天再跑：账还没修，但不重复刷屏
    second = wallet.audit_wallet(db, now=NOW)
    assert second["admins_notified"] == 0
    assert len(db.scalars(select(Notification).where(Notification.category == "finance")).all()) == 1

    # 次日仍然坏着，再提醒一次
    third = wallet.audit_wallet(db, now=NOW + timedelta(days=1))
    assert third["admins_notified"] == 1


def test_wallet_audit_does_not_notify_on_warnings_only(env):
    db, _ = env
    # 客户有余额但没钱包账户：属提示项，不该推通知
    customer = db.scalar(select(Customer).where(Customer.name == "客户甲"))
    customer.balance = 88.88
    db.commit()

    result = wallet.audit_wallet(db, now=NOW)
    assert result["blocking"] == 0
    assert result["warnings"] == 1
    assert result["orphan_customer_balance"] == 1
    assert result["admins_notified"] == 0
