"""收款闸门与收件箱消息顺序的回归用例。

两条规则（2026-11-09 定）：

1. **先收钱再发货**：余额/在线支付的订单未收款时不能确认、不能交付；线下转账
   （``contact_manager``）保留先确认后到账的现网流程，但审计里要留收款状态。
2. **长会话看到的是最新消息**：``order=desc`` 让第一页返回最近 N 条，
   避免会话超过 100 条后只看到最早的对话。
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import (
    Customer, InboxMessage, Node, Order, Product, Region, User, WalletAccount,
)
from app.models.base import Base, utcnow
from app.services.registration_service import registration_rate_limiter

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
    db.add(WalletAccount(user_id=customer_user.id, balance_cents=100000, currency="CNY"))
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
    client.node = node
    yield client
    registration_rate_limiter.clear()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_order(client, headers, payment_method):
    response = client.post(
        "/api/customer/orders",
        json={"product_id": client.product.id, "quantity": 1, "billing_days": 30,
              "payment_method": payment_method},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def delivery_payload(client):
    return {"node_id": client.node.id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}


def test_unpaid_online_order_cannot_be_confirmed_or_delivered(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    order = create_order(api, customer_headers, "online")
    assert order["status"] == "pending_payment"
    assert order["payment_status"] == "unpaid"

    confirm = api.post(f"/api/admin/orders/{order['id']}/confirm", headers=admin_headers)
    assert confirm.status_code == 409
    assert "尚未收款" in confirm.json()["detail"]

    # 即使状态被手工推到 pending_delivery，交付也必须重新校验收款。
    api.db.expire_all()
    db_order = api.db.get(Order, order["id"])
    db_order.status = "pending_delivery"
    db_order.confirmed_at = utcnow()
    api.db.commit()

    delivered = api.post(f"/api/admin/orders/{order['id']}/deliver", json=delivery_payload(api),
                         headers=admin_headers)
    assert delivered.status_code == 409
    assert "尚未收款" in delivered.json()["detail"]


def test_paid_order_can_be_confirmed_and_delivered(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    order = create_order(api, customer_headers, "balance")
    assert api.post(f"/api/wallet/orders/{order['id']}/pay-balance",
                    json={"idempotency_key": "gate-balance-0001"},
                    headers=customer_headers).status_code == 200

    confirm = api.post(f"/api/admin/orders/{order['id']}/confirm", headers=admin_headers)
    assert confirm.status_code == 200, confirm.text
    delivered = api.post(f"/api/admin/orders/{order['id']}/deliver", json=delivery_payload(api),
                         headers=admin_headers)
    assert delivered.status_code == 200, delivered.text


def test_contact_manager_order_keeps_offline_flow(api):
    """线下转账现实里就是先确认后到账，闸门不能把这条流程堵死。"""
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    order = create_order(api, customer_headers, "contact_manager")
    assert order["status"] == "pending_confirm"
    assert order["payment_status"] == "unpaid"

    confirm = api.post(f"/api/admin/orders/{order['id']}/confirm", headers=admin_headers)
    assert confirm.status_code == 200, confirm.text
    delivered = api.post(f"/api/admin/orders/{order['id']}/deliver", json=delivery_payload(api),
                         headers=admin_headers)
    assert delivered.status_code == 200, delivered.text


def test_inbox_messages_can_return_latest_first(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    created = api.post("/api/inbox/threads", json={"subject": "线路问题"}, headers=customer_headers)
    assert created.status_code == 201, created.text
    thread_id = created.json()["id"]

    api.db.expire_all()
    base = utcnow() - timedelta(hours=5)
    for index in range(5):
        api.db.add(InboxMessage(
            thread_id=thread_id,
            sender_user_id=api.users["customer"].id,
            sender_role="customer",
            content=f"第 {index + 1} 条",
            created_at=base + timedelta(minutes=index),
        ))
    api.db.commit()

    latest = api.get(f"/api/inbox/threads/{thread_id}/messages",
                     params={"page": 1, "page_size": 2, "order": "desc"},
                     headers=customer_headers)
    assert latest.status_code == 200, latest.text
    body = latest.json()
    assert body["total"] == 5
    assert [item["content"] for item in body["items"]] == ["第 5 条", "第 4 条"]

    # 默认行为不变：不传 order 仍是时间正序，老客户端不受影响。
    default = api.get(f"/api/inbox/threads/{thread_id}/messages",
                      params={"page": 1, "page_size": 2}, headers=customer_headers)
    assert [item["content"] for item in default.json()["items"]] == ["第 1 条", "第 2 条"]
