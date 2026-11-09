"""续费接支付 + 销售业绩按回款统计的回归用例。

两块业务口径在 2026-11-09 确认：

1. 销售业绩按「实际收款」算：未付款订单不计入，退款按退款发生时间冲减；
2. 续费单接余额/在线支付：付款成功即延长线路到期时间，不再依赖管理员二次确认；
   退款则回退天数并冲销佣金。
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import (
    Customer, CustomerLine, Node, Order, Product, Region, SalesCommissionEntry, User, WalletAccount,
)
from app.models.base import Base, utcnow
from app.services.registration_service import registration_rate_limiter


CUSTOMER_PASSWORD = "Customer_123456"
SALES_PASSWORD = "Sales_123456"
ADMIN_PASSWORD = "Admin_123456"


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
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


def create_renewal(client, headers, payment_method, billing_days=30):
    response = client.post(
        f"/api/customer/lines/{client.line.id}/renew",
        json={"billing_days": billing_days, "payment_method": payment_method},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_balance_renewal_extends_line_and_accrues_commission(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    renewal = create_renewal(api, customer_headers, "balance")
    # 余额/在线支付直接进入待支付，钱包页可以付；联系经理才落到 renewal 状态。
    assert renewal["status"] == "pending_payment"

    api.db.expire_all()
    original_expiry = api.db.get(CustomerLine, api.line.id).expires_at

    paid = api.post(f"/api/wallet/orders/{renewal['id']}/pay-balance",
                    json={"idempotency_key": "renew-balance-key-0001"}, headers=customer_headers)
    assert paid.status_code == 200, paid.text

    api.db.expire_all()
    order = api.db.get(Order, renewal["id"])
    assert order.status == "completed"
    assert order.payment_status == "paid"
    assert order.renewal_completed_at is not None
    assert api.db.get(CustomerLine, api.line.id).expires_at == original_expiry + timedelta(days=30)

    entry = api.db.scalar(select(SalesCommissionEntry).where(
        SalesCommissionEntry.order_id == renewal["id"],
        SalesCommissionEntry.amount_cents > 0,
    ))
    assert entry is not None and entry.amount_cents == 1500

    # 重复支付必须被拦住，不能再延长一次。
    again = api.post(f"/api/wallet/orders/{renewal['id']}/pay-balance",
                     json={"idempotency_key": "renew-balance-key-0002"}, headers=customer_headers)
    assert again.status_code == 200
    api.db.expire_all()
    assert api.db.get(CustomerLine, api.line.id).expires_at == original_expiry + timedelta(days=30)


def test_legacy_contact_manager_renewal_can_still_be_paid(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    renewal = create_renewal(api, customer_headers, "contact_manager")
    assert renewal["status"] == "renewal"

    api.db.expire_all()
    original_expiry = api.db.get(CustomerLine, api.line.id).expires_at
    paid = api.post(f"/api/wallet/orders/{renewal['id']}/pay-balance",
                    json={"idempotency_key": "renew-legacy-key-0001"}, headers=customer_headers)
    assert paid.status_code == 200, paid.text

    api.db.expire_all()
    assert api.db.get(Order, renewal["id"]).status == "completed"
    assert api.db.get(CustomerLine, api.line.id).expires_at == original_expiry + timedelta(days=30)


def test_sales_dashboard_counts_received_cash_only(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    sales_headers = login(api, "sales_a", SALES_PASSWORD)

    created = api.post("/api/customer/orders",
                       json={"product_id": api.product.id, "quantity": 1, "billing_days": 30,
                             "payment_method": "online"},
                       headers=customer_headers)
    assert created.status_code == 200, created.text
    order = created.json()
    assert order["status"] == "pending_payment"

    # 未付款：不计入业绩。
    dashboard = api.get("/api/sales/dashboard", headers=sales_headers).json()
    assert dashboard["monthly_sales"] == 0
    assert dashboard["monthly_renewals"] == 0

    paid = api.post(f"/api/wallet/orders/{order['id']}/pay-balance",
                    json={"idempotency_key": "purchase-balance-key-01"}, headers=customer_headers)
    assert paid.status_code == 200, paid.text
    dashboard = api.get("/api/sales/dashboard", headers=sales_headers).json()
    assert dashboard["monthly_sales"] == 100

    renewal = create_renewal(api, customer_headers, "balance")
    assert api.post(f"/api/wallet/orders/{renewal['id']}/pay-balance",
                    json={"idempotency_key": "renew-dashboard-key-01"},
                    headers=customer_headers).status_code == 200
    dashboard = api.get("/api/sales/dashboard", headers=sales_headers).json()
    assert dashboard["monthly_sales"] == 100
    assert dashboard["monthly_renewals"] == 100


def test_refunded_renewal_rolls_back_days_and_commission(api):
    customer_headers = login(api, "customer_a", CUSTOMER_PASSWORD)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    renewal = create_renewal(api, customer_headers, "balance")

    api.db.expire_all()
    original_expiry = api.db.get(CustomerLine, api.line.id).expires_at
    assert api.post(f"/api/wallet/orders/{renewal['id']}/pay-balance",
                    json={"idempotency_key": "renew-refund-key-0001"},
                    headers=customer_headers).status_code == 200
    api.db.expire_all()
    assert api.db.get(CustomerLine, api.line.id).expires_at == original_expiry + timedelta(days=30)

    refunded = api.post(f"/api/admin/finance/orders/{renewal['id']}/refund",
                        json={"refund_already_completed": True, "reason": "客户取消本次续费"},
                        headers=admin_headers)
    assert refunded.status_code == 200, refunded.text

    api.db.expire_all()
    assert api.db.get(Order, renewal["id"]).status == "refunded"
    assert api.db.get(CustomerLine, api.line.id).expires_at == original_expiry
    net = sum(api.db.scalars(select(SalesCommissionEntry.amount_cents).where(
        SalesCommissionEntry.order_id == renewal["id"],
    )).all())
    assert net == 0
