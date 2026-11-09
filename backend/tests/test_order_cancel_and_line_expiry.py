"""取消订单闸门与线路到期标记的回归用例。

两条规则在 2026-11-09 定下：

1. **钱没落定就不许关单**：存在 ``pending`` / ``manual_review`` 支付单时，客户和
   管理员都不能取消订单；迟到的支付回调也不能把已关闭的订单改回已付款（续费单
   更不能因此延长线路）。
2. **到期必须落状态**：线路过期后要由任务写成 ``expired``，且只翻 ``active``——
   人工停用和已删除的线路不能被时间任务改写；过期线路仍要留在销售外呼名单里。
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.finance_routes import _record_paid_order
from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import (
    Customer, CustomerLine, Node, Notification, Order, PaymentOrder, Product, Region, User,
)
from app.models.base import Base, utcnow
from app.services.order_service import settle_renewal
from app.services.renewal_outreach_service import outreach_queue
from app.services.registration_service import registration_rate_limiter
from app.tasks import lines as line_tasks

CUSTOMER_PASSWORD = "Customer_123456"
ADMIN_PASSWORD = "Admin_123456"
SALES_PASSWORD = "Sales_123456"


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = Session()

    region = Region(name="香港", country_code="HK")
    db.add(region)
    db.flush()
    admin = User(username="admin", password_hash=hash_password(ADMIN_PASSWORD), role="admin", display_name="管理员")
    sales = User(username="sales_a", password_hash=hash_password(SALES_PASSWORD), role="sales", display_name="销售 A")
    customer_user = User(username="customer_a", password_hash=hash_password(CUSTOMER_PASSWORD),
                         role="customer", display_name="客户 A")
    db.add_all([admin, sales, customer_user])
    db.flush()
    customer = Customer(name="客户 A", email="a@example.com", sales_id=sales.id, user_id=customer_user.id)
    product = Product(name="香港专线", product_type="直播线路", price=100, billing_days=30, region_id=region.id)
    node = Node(name="香港节点", server="127.0.0.1", port=1, protocol="VLESS", region_id=region.id,
                health_status="healthy")
    db.add_all([customer, product, node])
    db.flush()
    line = CustomerLine(customer_id=customer.id, node_id=node.id, product_id=product.id,
                        started_at=utcnow() - timedelta(days=20), expires_at=utcnow() + timedelta(days=10))
    db.add(line)
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.db = db
    client.users = {"admin": admin, "sales": sales, "customer": customer_user}
    client.customer = customer
    client.product = product
    client.line = line
    yield client
    registration_rate_limiter.clear()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_order(client, headers, payment_method="online"):
    response = client.post(
        "/api/customer/orders",
        json={"product_id": client.product.id, "quantity": 1, "billing_days": 30,
              "payment_method": payment_method},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def attach_payment(client, order_id, status="pending", out_trade_no=None):
    payment = PaymentOrder(
        out_trade_no=out_trade_no or f"PAY-{order_id}-{status}",
        idempotency_key=f"idem-{order_id}-{status}",
        user_id=client.customer.user_id,
        purpose="order_payment",
        order_id=order_id,
        amount_cents=10000,
        channel="wechat",
        status=status,
    )
    client.db.add(payment)
    client.db.commit()
    return payment


def test_customer_cannot_cancel_order_while_payment_pending(api):
    headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    order = create_order(api, headers)
    attach_payment(api, order["id"], "pending")

    blocked = api.post(f"/api/customer/orders/{order['id']}/cancel", headers=headers)
    assert blocked.status_code == 409
    assert "支付中" in blocked.json()["detail"]

    api.db.expire_all()
    assert api.db.get(Order, order["id"]).status == "pending_payment"


def test_customer_cannot_cancel_order_under_manual_review(api):
    headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    order = create_order(api, headers)
    attach_payment(api, order["id"], "manual_review")

    blocked = api.post(f"/api/customer/orders/{order['id']}/cancel", headers=headers)
    assert blocked.status_code == 409
    assert "财务核对" in blocked.json()["detail"]


def test_customer_can_cancel_without_payment_record(api):
    headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    order = create_order(api, headers, payment_method="contact_manager")

    cancelled = api.post(f"/api/customer/orders/{order['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


def test_admin_cannot_cancel_paid_order(api):
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    order = create_order(api, customer_headers, payment_method="contact_manager")
    api.db.expire_all()
    db_order = api.db.get(Order, order["id"])
    db_order.status = "paid"
    db_order.payment_status = "paid"
    api.db.commit()

    blocked = api.post(f"/api/admin/orders/{order['id']}/cancel", headers=admin_headers)
    assert blocked.status_code == 409
    assert "退款" in blocked.json()["detail"]


def test_late_callback_does_not_revive_cancelled_order(api):
    headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    order = create_order(api, headers)
    assert api.post(f"/api/customer/orders/{order['id']}/cancel", headers=headers).status_code == 200

    # 本地已取消之后，支付平台的回调才到。
    payment = attach_payment(api, order["id"], "pending", out_trade_no="PAY-LATE-0001")
    api.db.expire_all()
    db_payment = api.db.get(PaymentOrder, payment.id)
    _record_paid_order(api.db, db_payment, api.customer, None)
    api.db.commit()

    api.db.expire_all()
    assert api.db.get(Order, order["id"]).status == "cancelled"
    assert api.db.get(Order, order["id"]).payment_status == "unpaid"
    assert api.db.get(PaymentOrder, payment.id).status == "manual_review"

    admin_id = api.users["admin"].id
    notice = api.db.scalar(select(Notification).where(
        Notification.user_id == admin_id,
        Notification.content.contains("PAY-LATE-0001"),
    ))
    assert notice is not None


def test_cancelled_renewal_cannot_extend_line(api):
    headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    renewal = api.post(
        f"/api/customer/lines/{api.line.id}/renew",
        json={"billing_days": 30, "payment_method": "contact_manager"},
        headers=headers,
    )
    assert renewal.status_code == 200, renewal.text

    api.db.expire_all()
    order = api.db.get(Order, renewal.json()["id"])
    order.status = "cancelled"
    api.db.commit()
    original_expiry = api.db.get(CustomerLine, api.line.id).expires_at

    with pytest.raises(HTTPException) as excinfo:
        settle_renewal(api.db, order, "paid")
    assert excinfo.value.status_code == 409

    api.db.rollback()
    api.db.expire_all()
    assert api.db.get(CustomerLine, api.line.id).expires_at == original_expiry


def _line(api, *, status="active", expires_delta_days=-1):
    line = CustomerLine(
        customer_id=api.customer.id,
        node_id=api.line.node_id,
        product_id=api.product.id,
        started_at=utcnow() - timedelta(days=40),
        expires_at=utcnow() + timedelta(days=expires_delta_days),
        status=status,
    )
    api.db.add(line)
    api.db.flush()
    return line


def test_expire_lines_only_touches_active(api):
    overdue = _line(api, expires_delta_days=-1)
    future = _line(api, expires_delta_days=5)
    disabled = _line(api, status="disabled", expires_delta_days=-1)
    deleted = _line(api, status="deleted", expires_delta_days=-1)
    api.db.commit()

    counts = line_tasks.expire_lines(api.db, now=utcnow())
    api.db.commit()

    assert counts["expired"] == 1
    assert api.db.get(CustomerLine, overdue.id).status == "expired"
    assert api.db.get(CustomerLine, future.id).status == "active"
    assert api.db.get(CustomerLine, disabled.id).status == "disabled"
    assert api.db.get(CustomerLine, deleted.id).status == "deleted"


def test_expire_lines_is_idempotent_and_notifies_once(api):
    line = _line(api, expires_delta_days=-2)
    api.db.commit()

    first = line_tasks.expire_lines(api.db, now=utcnow())
    api.db.commit()
    assert first["expired"] == 1
    assert first["notified"] == 1

    second = line_tasks.expire_lines(api.db, now=utcnow())
    api.db.commit()
    assert second["expired"] == 0
    assert second["notified"] == 0

    notices = api.db.scalars(select(Notification).where(
        Notification.user_id == api.customer.user_id,
        Notification.title == "线路已到期",
        Notification.content.contains(f"线路 {line.id} "),
    )).all()
    assert len(notices) == 1


def test_expire_lines_respects_grace_hours(api):
    _line(api, expires_delta_days=-1)
    api.db.commit()

    counts = line_tasks.expire_lines(api.db, now=utcnow(), grace_hours=48)
    api.db.commit()
    assert counts["expired"] == 0


def test_outreach_queue_includes_expired_lines(api):
    expired_line = _line(api, status="expired", expires_delta_days=-1)
    active_line = _line(api, status="active", expires_delta_days=3)
    api.db.commit()

    items = outreach_queue(api.db, user=api.users["sales"], days=30, overdue_grace_days=7)
    ids = {item["line_id"] for item in items}
    assert expired_line.id in ids
    assert active_line.id in ids
