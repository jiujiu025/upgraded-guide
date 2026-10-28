from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import AuditLog, Customer, CustomerLine, HealthIncident, InboxMessage, InboxThread, Node, Notification, Order, Product, Region, User
from app.models.base import Base
from app.models.base import utcnow
from app.services.health_service import check_node
from app.services.order_service import claim_delivery_slot
from app.services.registration_service import registration_rate_limiter


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    region_hk = Region(name="香港", country_code="HK")
    region_jp = Region(name="日本", country_code="JP")
    db.add_all([region_hk, region_jp])
    db.flush()
    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 B")
    customer_a_user = User(username="customer_a", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户 A")
    customer_b_user = User(username="customer_b", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户 B")
    locked = User(username="locked", password_hash=hash_password("Locked_123456"), role="customer", display_name="锁定用户")
    db.add_all([admin, sales_a, sales_b, customer_a_user, customer_b_user, locked])
    db.flush()
    customer_a = Customer(name="客户 A", email="a@example.com", sales_id=sales_a.id, user_id=customer_a_user.id)
    customer_b = Customer(name="客户 B", email="b@example.com", sales_id=sales_b.id, user_id=customer_b_user.id)
    unbound = Customer(name="未绑定", email="unbound@example.com", sales_id=sales_a.id)
    product_hk = Product(name="香港产品", product_type="直播线路", price=100, billing_days=30, region_id=region_hk.id)
    product_jp = Product(name="日本产品", product_type="直播线路", price=100, billing_days=30, region_id=region_jp.id)
    node_hk = Node(name="香港节点", server="127.0.0.1", port=1, protocol="VLESS", region_id=region_hk.id, health_status="healthy", health_latency_ms=20)
    node_jp = Node(name="日本节点", server="127.0.0.1", port=2, protocol="VLESS", region_id=region_jp.id, health_status="offline")
    db.add_all([customer_a, customer_b, unbound, product_hk, product_jp, node_hk, node_jp])
    db.flush()
    line_a = CustomerLine(customer_id=customer_a.id, node_id=node_hk.id, product_id=product_hk.id, started_at=utcnow(), expires_at=utcnow() + timedelta(days=30))
    line_b = CustomerLine(customer_id=customer_b.id, node_id=node_jp.id, product_id=product_jp.id, started_at=utcnow(), expires_at=utcnow() + timedelta(days=30))
    db.add_all([line_a, line_b])
    db.add(HealthIncident(node_id=node_hk.id, fault_type="tcp", reason="测试故障", started_at=utcnow(), status="open"))
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
    client.session_factory = Session
    client.users = {u.username: u for u in [admin, sales_a, sales_b, customer_a_user, customer_b_user, locked]}
    client.products = {"hk": product_hk, "jp": product_jp}
    client.nodes = {"hk": node_hk, "jp": node_jp}
    client.customers = {"a": customer_a, "b": customer_b, "unbound": unbound}
    yield client
    registration_rate_limiter.clear()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def token(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token_value):
    return {"Authorization": f"Bearer {token_value}"}


def test_customer_scope_and_order_identity(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    orders = api.get("/api/orders", headers=auth(customer_token))
    assert orders.status_code == 200
    created = api.post("/api/orders", json={"product_id": api.products["hk"].id}, headers=auth(customer_token))
    assert created.status_code == 200
    assert created.json()["customer_id"] == api.customers["a"].id
    assert created.json()["customer_id"] != api.users["customer_a"].id


def test_customer_line_idor_and_health_scope(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    lines = api.get("/api/lines", headers=auth(customer_token))
    assert lines.status_code == 200
    assert {item["customer_id"] for item in lines.json()} == {api.customers["a"].id}
    other_line_id = api.db.scalar(select(CustomerLine.id).where(CustomerLine.customer_id == api.customers["b"].id))
    assert api.get(f"/api/lines/{other_line_id}", headers=auth(customer_token)).status_code == 404
    summary = api.get("/api/health/summary", headers=auth(customer_token)).json()
    assert summary["total"] == 1
    assert summary["open_incidents"] == 1


def test_sales_scope_and_incident_scope(api):
    sales_token = token(api, "sales_a", "Sales_123456")
    lines = api.get("/api/health/lines", headers=auth(sales_token))
    assert lines.status_code == 200
    assert {item["id"] for item in lines.json()} == {api.nodes["hk"].id}
    incidents = api.get("/api/health/incidents", headers=auth(sales_token))
    assert incidents.status_code == 200
    assert len(incidents.json()) == 1


def test_delivery_state_region_and_duplicate_guards(api):
    admin_token = token(api, "admin", "Admin_123456")
    customer = api.customers["a"]
    product = api.products["hk"]
    node = api.nodes["hk"]
    order = Order(order_no="ORD-DELIVER-1", customer_id=customer.id, product_id=product.id, amount=product.price, status="pending_delivery")
    api.db.add(order)
    api.db.commit()
    payload = {"customer_id": customer.id, "node_id": node.id, "product_id": product.id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
    delivered = api.post(f"/api/orders/{order.id}/deliver", json=payload, headers=auth(admin_token))
    assert delivered.status_code == 200
    assert api.post(f"/api/orders/{order.id}/deliver", json=payload, headers=auth(admin_token)).status_code == 409

    bad_order = Order(order_no="ORD-DELIVER-2", customer_id=customer.id, product_id=product.id, amount=product.price, status="pending_confirm")
    api.db.add(bad_order)
    api.db.commit()
    assert api.post(f"/api/orders/{bad_order.id}/deliver", json=payload, headers=auth(admin_token)).status_code == 409

    mismatch = Order(order_no="ORD-DELIVER-3", customer_id=customer.id, product_id=product.id, amount=product.price, status="pending_delivery")
    api.db.add(mismatch)
    api.db.commit()
    mismatch_payload = {**payload, "node_id": api.nodes["jp"].id}
    assert api.post(f"/api/orders/{mismatch.id}/deliver", json=mismatch_payload, headers=auth(admin_token)).status_code == 400


def test_unbound_customer_delivery_and_login_lock_unlock(api):
    admin_token = token(api, "admin", "Admin_123456")
    unbound = api.customers["unbound"]
    product = api.products["hk"]
    node = api.nodes["hk"]
    order = Order(order_no="ORD-UNBOUND", customer_id=unbound.id, product_id=product.id, amount=product.price, status="pending_delivery")
    api.db.add(order)
    api.db.commit()
    payload = {"customer_id": unbound.id, "node_id": node.id, "product_id": product.id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
    assert api.post(f"/api/orders/{order.id}/deliver", json=payload, headers=auth(admin_token)).status_code == 400
    assert api.db.scalar(select(Notification).where(Notification.user_id == unbound.id)) is None

    for _ in range(5):
        assert api.post("/api/auth/login", json={"username": "locked", "password": "wrong-password"}).status_code == 401
    locked_response = api.post("/api/auth/login", json={"username": "locked", "password": "wrong-password"})
    assert locked_response.status_code == 401
    api.db.expire_all()
    assert api.db.get(User, api.users["locked"].id).failed_login_count == 5
    assert api.post(f"/api/admin/users/{api.users['locked'].id}/unlock-login", headers=auth(admin_token)).status_code == 200
    assert api.post("/api/auth/login", json={"username": "locked", "password": "Locked_123456"}).status_code == 200


def test_login_failure_messages_do_not_reveal_account_state(api):
    unknown = api.post("/api/auth/login", json={"username": "missing-user", "password": "Wrong_123456"})
    wrong_password = api.post("/api/auth/login", json={"username": "customer_a", "password": "Wrong_123456"})
    locked_user = api.users["locked"]
    locked_user.locked_until = utcnow() + timedelta(minutes=15)
    api.db.commit()
    locked = api.post("/api/auth/login", json={"username": "locked", "password": "Locked_123456"})
    assert {unknown.status_code, wrong_password.status_code, locked.status_code} == {401}
    assert unknown.json()["detail"] == wrong_password.json()["detail"] == locked.json()["detail"] == "用户名或密码错误"


def test_production_default_jwt_secret_rejected():
    with pytest.raises(ValueError, match="默认 JWT_SECRET_KEY"):
        Settings(environment="production", jwt_secret_key="change-this-in-production")


def test_public_catalog_and_backend_price_calculation(api):
    product = api.products["hk"]
    region_id = product.region_id
    assert api.get("/api/public/products").status_code == 200
    detail = api.get(f"/api/public/products/{product.id}")
    assert detail.status_code == 200
    assert "server" not in detail.json()
    preview = api.post("/api/public/orders/preview", json={"product_id": product.id, "region_id": region_id, "quantity": 2, "billing_days": 30})
    assert preview.status_code == 200
    assert preview.json()["total_amount"] == 200
    tampered = api.post("/api/public/orders/preview", json={"product_id": product.id, "region_id": region_id, "quantity": 2, "billing_days": 30, "total_amount": 0.01})
    assert tampered.status_code == 200
    assert tampered.json()["total_amount"] == 200


def test_customer_order_rejects_price_and_customer_tampering(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    product = api.products["hk"]
    response = api.post("/api/customer/orders", json={"product_id": product.id, "quantity": 2, "billing_days": 30, "amount": 0.01, "customer_id": api.customers["b"].id}, headers=auth(customer_token))
    assert response.status_code == 200
    assert response.json()["customer_id"] == api.customers["a"].id
    assert response.json()["total_amount"] == 200
    created = api.post("/api/customer/orders", json={"product_id": product.id, "quantity": 2, "billing_days": 30}, headers=auth(customer_token))
    assert created.status_code == 200
    body = created.json()
    assert body["customer_id"] == api.customers["a"].id
    assert body["unit_price"] == 100
    assert body["total_amount"] == 200
    assert body["product_name"] == product.name
    assert body["region_name"] == "香港"


def test_sales_workbench_isolation(api):
    sales_token = token(api, "sales_a", "Sales_123456")
    customers = api.get("/api/sales/customers", headers=auth(sales_token))
    assert customers.status_code == 200
    assert {item["id"] for item in customers.json()} == {api.customers["a"].id, api.customers["unbound"].id}
    orders = api.get("/api/sales/orders", headers=auth(sales_token))
    assert orders.status_code == 200
    assert all(item["customer_id"] in {api.customers["a"].id, api.customers["unbound"].id} for item in orders.json())
    expiring = api.get("/api/sales/expiring-lines?days=60", headers=auth(sales_token))
    assert expiring.status_code == 200
    assert len(expiring.json()) == 1


def test_sales_cannot_confirm_order_through_legacy_route(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    sales_token = token(api, "sales_a", "Sales_123456")
    created = api.post("/api/customer/orders", json={"product_id": api.products["hk"].id, "billing_days": 30}, headers=auth(customer_token))
    assert created.status_code == 200
    assert api.post(f"/api/orders/{created.json()['id']}/confirm", headers=auth(sales_token)).status_code == 403


def test_admin_delivery_becomes_visible_and_notifies_only_parties(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    admin_token = token(api, "admin", "Admin_123456")
    product = api.products["hk"]
    created = api.post("/api/customer/orders", json={"product_id": product.id, "quantity": 1, "billing_days": 30}, headers=auth(customer_token))
    assert created.status_code == 200
    order_id = created.json()["id"]
    assert api.post(f"/api/admin/orders/{order_id}/confirm", headers=auth(admin_token)).status_code == 200
    delivery = api.post(f"/api/admin/orders/{order_id}/deliver", json={"node_id": api.nodes["hk"].id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}, headers=auth(admin_token))
    assert delivery.status_code == 200
    line_id = delivery.json()["id"]
    customer_line = api.get(f"/api/customer/lines/{line_id}", headers=auth(customer_token))
    assert customer_line.status_code == 200
    assert customer_line.json()["subscription_url"] is None
    assert api.post(f"/api/admin/orders/{order_id}/deliver", json={"node_id": api.nodes["hk"].id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}, headers=auth(admin_token)).status_code == 409
    api.db.expire_all()
    order = api.db.get(Order, order_id)
    assert order.delivered_line_id == line_id
    recipients = set(api.db.scalars(select(Notification.user_id).where(Notification.category == "delivery")).all())
    assert recipients == {api.users["customer_a"].id, api.users["sales_a"].id}


def test_renewal_is_idempotent_and_does_not_extend_line(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    line = api.db.scalar(select(CustomerLine).where(CustomerLine.customer_id == api.customers["a"].id))
    original_expiry = line.expires_at
    first = api.post(f"/api/customer/lines/{line.id}/renew", json={"billing_days": 30}, headers=auth(customer_token))
    assert first.status_code == 200
    assert first.json()["order_type"] == "renewal"
    assert api.post(f"/api/customer/lines/{line.id}/renew", json={"billing_days": 30}, headers=auth(customer_token)).status_code == 409
    api.db.expire_all()
    assert api.db.get(CustomerLine, line.id).expires_at == original_expiry


def test_customer_line_request_creates_order_without_creating_line(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    before = api.db.scalar(select(func.count()).select_from(CustomerLine))
    response = api.post(
        "/api/customer/lines",
        json={"product_id": api.products["hk"].id, "quantity": 1, "billing_days": 30},
        headers=auth(customer_token),
    )
    assert response.status_code == 200
    assert response.json()["customer_id"] == api.customers["a"].id
    assert response.json()["status"] == "pending_confirm"
    api.db.expire_all()
    assert api.db.scalar(select(func.count()).select_from(CustomerLine)) == before


def test_delivery_rejects_disabled_product(api):
    admin_token = token(api, "admin", "Admin_123456")
    product = api.products["hk"]
    product.is_active = False
    order = Order(
        order_no="ORD-DISABLED-PRODUCT",
        customer_id=api.customers["a"].id,
        product_id=product.id,
        amount=product.price,
        status="pending_delivery",
        region_id=product.region_id,
    )
    api.db.add(order)
    api.db.commit()
    response = api.post(
        f"/api/admin/orders/{order.id}/deliver",
        json={"node_id": api.nodes["hk"].id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        headers=auth(admin_token),
    )
    assert response.status_code == 400


def test_health_failure_notifies_only_impacted_customer_and_sales(api, monkeypatch):
    node = Node(name="异常节点", server="127.0.0.1", port=9, protocol="VLESS", region_id=api.products["hk"].region_id)
    api.db.add(node)
    api.db.flush()
    api.db.add(CustomerLine(customer_id=api.customers["a"].id, node_id=node.id, product_id=api.products["hk"].id, started_at=utcnow(), expires_at=utcnow() + timedelta(days=10)))
    api.db.commit()

    def fail_connection(*args, **kwargs):
        raise OSError("连接失败")

    monkeypatch.setattr("app.services.health_service.socket.create_connection", fail_connection)
    check_node(api.db, node)
    recipients = set(api.db.scalars(select(Notification.user_id).where(Notification.category == "health")).all())
    assert recipients == {api.users["customer_a"].id, api.users["sales_a"].id}


def test_unauthenticated_customer_cannot_create_order(api):
    response = api.post("/api/customer/orders", json={"product_id": api.products["hk"].id, "region_id": api.products["hk"].region_id, "billing_days": 30})
    assert response.status_code == 401


def test_customer_cannot_read_other_customer_order(api):
    token_a = token(api, "customer_a", "Customer_123456")
    token_b = token(api, "customer_b", "Customer_123456")
    created = api.post("/api/customer/orders", json={"product_id": api.products["hk"].id, "region_id": api.products["hk"].region_id, "billing_days": 30}, headers=auth(token_a))
    assert created.status_code == 200
    assert api.get(f"/api/customer/orders/{created.json()['id']}", headers=auth(token_b)).status_code == 404


def test_customer_cannot_read_or_edit_other_customer_line(api):
    token_a = token(api, "customer_a", "Customer_123456")
    other_line_id = api.db.scalar(select(CustomerLine.id).where(CustomerLine.customer_id == api.customers["b"].id))
    assert api.get(f"/api/customer/lines/{other_line_id}", headers=auth(token_a)).status_code == 404
    assert api.patch(f"/api/customer/lines/{other_line_id}/note", json={"customer_note": "越权修改"}, headers=auth(token_a)).status_code == 404


def test_sales_scope_and_cannot_modify_price(api):
    sales_token = token(api, "sales_a", "Sales_123456")
    customers = api.get("/api/sales/customers", headers=auth(sales_token))
    assert customers.status_code == 200
    assert {item["id"] for item in customers.json()} == {api.customers["a"].id, api.customers["unbound"].id}
    response = api.patch(f"/api/products/{api.products['hk'].id}", json={"price": 0.01}, headers=auth(sales_token))
    assert response.status_code in {404, 405}


def test_order_ignores_tampered_price_and_customer(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    payload = {
        "product_id": api.products["hk"].id,
        "region_id": api.products["hk"].region_id,
        "billing_days": 30,
        "quantity": 2,
        "unit_price": 0.01,
        "total_amount": 0.01,
        "customer_id": api.customers["b"].id,
    }
    response = api.post("/api/customer/orders", json=payload, headers=auth(customer_token))
    assert response.status_code == 200, response.text
    order = response.json()
    assert order["customer_id"] == api.customers["a"].id
    assert order["unit_price"] == api.products["hk"].price
    assert order["total_amount"] == api.products["hk"].price * 2


def test_duplicate_renewal_is_rejected_without_changing_expiry(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    line = api.db.scalar(select(CustomerLine).where(CustomerLine.customer_id == api.customers["a"].id))
    original_expiry = line.expires_at
    payload = {"billing_days": 30, "payment_method": "contact_manager"}
    first = api.post(f"/api/customer/lines/{line.id}/renew", json=payload, headers=auth(customer_token))
    assert first.status_code == 200, first.text
    assert api.post(f"/api/customer/lines/{line.id}/renew", json=payload, headers=auth(customer_token)).status_code == 409
    api.db.expire_all()
    assert api.db.get(CustomerLine, line.id).expires_at == original_expiry


def test_illegal_order_transition_is_rejected(api):
    admin_token = token(api, "admin", "Admin_123456")
    customer_token = token(api, "customer_a", "Customer_123456")
    created = api.post("/api/customer/orders", json={"product_id": api.products["hk"].id, "region_id": api.products["hk"].region_id, "billing_days": 30}, headers=auth(customer_token)).json()
    payload = {"node_id": api.nodes["hk"].id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
    assert api.post(f"/api/admin/orders/{created['id']}/deliver", json=payload, headers=auth(admin_token)).status_code == 409


def test_admin_delivery_becomes_visible_to_customer(api):
    admin_token = token(api, "admin", "Admin_123456")
    customer_token = token(api, "customer_a", "Customer_123456")
    created = api.post("/api/customer/orders", json={"product_id": api.products["hk"].id, "region_id": api.products["hk"].region_id, "billing_days": 30}, headers=auth(customer_token)).json()
    assert api.post(f"/api/admin/orders/{created['id']}/confirm", headers=auth(admin_token)).status_code == 200
    payload = {"node_id": api.nodes["hk"].id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
    delivered = api.post(f"/api/admin/orders/{created['id']}/deliver", json=payload, headers=auth(admin_token))
    assert delivered.status_code == 200, delivered.text
    detail = api.get(f"/api/customer/lines/{delivered.json()['id']}", headers=auth(customer_token))
    assert detail.status_code == 200
    assert detail.json()["subscription_url"] is None
    assert detail.json()["node_link"] is None


def test_order_notifications_only_reach_correct_parties(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    response = api.post("/api/customer/orders", json={"product_id": api.products["hk"].id, "region_id": api.products["hk"].region_id, "billing_days": 30}, headers=auth(customer_token))
    assert response.status_code == 200
    api.db.expire_all()
    recipients = set(api.db.scalars(select(Notification.user_id).where(Notification.category == "order")).all())
    assert api.users["customer_a"].id in recipients
    assert api.users["sales_a"].id in recipients
    assert api.users["customer_b"].id not in recipients
    assert api.users["sales_b"].id not in recipients


def register_payload(username: str) -> dict:
    return {
        "username": username,
        "password": "StrongPass_123!",
        "name": f"注册客户 {username}",
        "email": f"{username}@example.com",
        "phone": "13800000000",
    }


def test_public_register_creates_bound_customer(api):
    response = api.post("/api/public/register", json=register_payload("new_customer"))
    assert response.status_code == 201, response.text
    assert response.json()["user"]["role"] == "customer"
    api.db.expire_all()
    user = api.db.scalar(select(User).where(User.username == "new_customer"))
    customer = api.db.scalar(select(Customer).where(Customer.user_id == user.id))
    assert customer is not None
    assert customer.sales_id is None


def test_public_register_rejects_privileged_fields_and_weak_password(api):
    privileged = {**register_payload("role_attack"), "role": "admin", "sales_id": api.users["sales_a"].id, "customer_id": 999}
    assert api.post("/api/public/register", json=privileged).status_code == 422
    weak = {**register_payload("weak_password"), "password": "abcdefghij"}
    assert api.post("/api/public/register", json=weak).status_code == 422
    assert api.db.scalar(select(User).where(User.username.in_({"role_attack", "weak_password"}))) is None


def test_public_register_conflict_uses_generic_error(api):
    response = api.post("/api/public/register", json={**register_payload("admin"), "email": "other@example.com"})
    assert response.status_code == 400
    assert response.json()["detail"] == "无法完成注册，请检查提交信息"


def test_public_register_rate_limit(api):
    for index in range(5):
        response = api.post("/api/public/register", json=register_payload(f"limited_{index}"))
        assert response.status_code == 201, response.text
    blocked = api.post("/api/public/register", json=register_payload("limited_blocked"))
    assert blocked.status_code == 429


def test_public_register_rolls_back_user_when_customer_creation_fails(api, monkeypatch):
    session_class = api.session_factory.class_
    original_flush = session_class.flush
    failed = False

    def fail_registration_flush(session, *args, **kwargs):
        nonlocal failed
        registering = any(isinstance(item, User) and item.username == "rollback_customer" for item in session.new)
        result = original_flush(session, *args, **kwargs)
        if registering and not failed:
            failed = True
            raise RuntimeError("模拟客户档案创建失败")
        return result

    monkeypatch.setattr(session_class, "flush", fail_registration_flush)
    response = api.post("/api/public/register", json=register_payload("rollback_customer"))
    assert response.status_code == 503
    api.db.expire_all()
    assert api.db.scalar(select(User).where(User.username == "rollback_customer")) is None
    assert api.db.scalar(select(Customer).where(Customer.email == "rollback_customer@example.com")) is None


def test_complete_renewal_extends_once_and_requires_manual_confirmation(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    admin_token = token(api, "admin", "Admin_123456")
    line = api.db.scalar(select(CustomerLine).where(CustomerLine.customer_id == api.customers["a"].id))
    original_expiry = line.expires_at
    renewal = api.post(
        f"/api/customer/lines/{line.id}/renew",
        json={"billing_days": 30, "payment_method": "contact_manager"},
        headers=auth(customer_token),
    ).json()
    endpoint = f"/api/admin/orders/{renewal['id']}/complete-renewal"
    assert api.post(endpoint, json={"manually_confirmed": True}, headers=auth(customer_token)).status_code == 403
    assert api.post(endpoint, json={"manually_confirmed": False}, headers=auth(admin_token)).status_code == 409
    completed = api.post(endpoint, json={"manually_confirmed": True}, headers=auth(admin_token))
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["payment_status"] == "manually_confirmed"
    api.db.expire_all()
    assert api.db.get(CustomerLine, line.id).expires_at == original_expiry + timedelta(days=30)
    repeated = api.post(endpoint, json={"manually_confirmed": True}, headers=auth(admin_token))
    assert repeated.status_code == 200
    api.db.expire_all()
    assert api.db.get(CustomerLine, line.id).expires_at == original_expiry + timedelta(days=30)


def test_expired_line_renewal_starts_from_current_time(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    admin_token = token(api, "admin", "Admin_123456")
    line = api.db.scalar(select(CustomerLine).where(CustomerLine.customer_id == api.customers["a"].id))
    line.expires_at = utcnow() - timedelta(days=10)
    api.db.commit()
    renewal = api.post(f"/api/customer/lines/{line.id}/renew", json={"billing_days": 30}, headers=auth(customer_token)).json()
    before = utcnow()
    response = api.post(f"/api/admin/orders/{renewal['id']}/complete-renewal", json={"manually_confirmed": True}, headers=auth(admin_token))
    after = utcnow()
    assert response.status_code == 200
    api.db.expire_all()
    renewed_expiry = api.db.get(CustomerLine, line.id).expires_at
    assert before + timedelta(days=30) <= renewed_expiry <= after + timedelta(days=30)


def test_complete_renewal_rejects_line_from_other_customer(api):
    admin_token = token(api, "admin", "Admin_123456")
    other_line = api.db.scalar(select(CustomerLine).where(CustomerLine.customer_id == api.customers["b"].id))
    order = Order(
        order_no="ORD-RENEWAL-IDOR",
        customer_id=api.customers["a"].id,
        product_id=api.products["hk"].id,
        amount=100,
        status="renewal",
        order_type="renewal",
        renewal_line_id=other_line.id,
        billing_days=30,
    )
    api.db.add(order)
    api.db.commit()
    response = api.post(f"/api/admin/orders/{order.id}/complete-renewal", json={"manually_confirmed": True}, headers=auth(admin_token))
    assert response.status_code == 409


def test_multi_quantity_order_partial_and_full_delivery(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    admin_token = token(api, "admin", "Admin_123456")
    created = api.post(
        "/api/customer/orders",
        json={"product_id": api.products["hk"].id, "quantity": 2, "billing_days": 30},
        headers=auth(customer_token),
    ).json()
    assert api.post(f"/api/admin/orders/{created['id']}/confirm", headers=auth(admin_token)).status_code == 200
    payload = {"node_id": api.nodes["hk"].id, "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
    assert api.post(f"/api/admin/orders/{created['id']}/deliver", json=payload, headers=auth(admin_token)).status_code == 200
    partial = api.get(f"/api/customer/orders/{created['id']}", headers=auth(customer_token)).json()
    assert partial["status"] == "partially_delivered"
    assert partial["delivered_quantity"] == 1
    assert api.post(f"/api/admin/orders/{created['id']}/deliver", json=payload, headers=auth(admin_token)).status_code == 200
    completed = api.get(f"/api/customer/orders/{created['id']}", headers=auth(customer_token)).json()
    assert completed["status"] == "delivered"
    assert completed["delivered_quantity"] == 2
    assert api.post(f"/api/admin/orders/{created['id']}/deliver", json=payload, headers=auth(admin_token)).status_code == 409
    api.db.expire_all()
    assert api.db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.order_id == created["id"])) == 2


def test_concurrent_delivery_claims_never_exceed_quantity(tmp_path):
    database_path = (tmp_path / "delivery.db").as_posix()
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        region = Region(name="并发地区", country_code="CC")
        db.add(region)
        db.flush()
        user = User(username="concurrent_customer", password_hash="unused", role="customer", display_name="并发客户")
        db.add(user)
        db.flush()
        customer = Customer(name="并发客户", email="concurrent@example.com", user_id=user.id)
        product = Product(name="并发产品", product_type="直播线路", price=100, billing_days=30, region_id=region.id)
        node = Node(name="并发节点", server="127.0.0.1", port=1, protocol="VLESS", region_id=region.id)
        db.add_all([customer, product, node])
        db.flush()
        order = Order(order_no="ORD-CONCURRENT", customer_id=customer.id, product_id=product.id, amount=200, quantity=2, status="pending_delivery")
        db.add(order)
        db.commit()
        order_id, customer_id, product_id, node_id = order.id, customer.id, product.id, node.id

    barrier = Barrier(4)

    def deliver_once() -> bool:
        with session_factory() as db:
            barrier.wait()
            claimed = claim_delivery_slot(db, order_id, utcnow())
            if claimed:
                db.add(CustomerLine(customer_id=customer_id, product_id=product_id, node_id=node_id, order_id=order_id, started_at=utcnow(), expires_at=utcnow() + timedelta(days=30)))
            db.commit()
            return claimed

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: deliver_once(), range(4)))
    with session_factory() as db:
        stored_order = db.get(Order, order_id)
        line_count = db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.order_id == order_id))
        assert sum(results) == 2
        assert stored_order.delivered_quantity == 2
        assert stored_order.status == "delivered"
        assert line_count == 2
    engine.dispose()


def create_inbox_thread(api, username: str, password: str, **overrides) -> dict:
    payload = {"subject": f"{username} 的服务咨询", **overrides}
    response = api.post("/api/inbox/threads", json=payload, headers=auth(token(api, username, password)))
    assert response.status_code == 201, response.text
    return response.json()


def test_inbox_customer_idor_and_identity_binding(api):
    thread = create_inbox_thread(api, "customer_a", "Customer_123456")
    token_a = token(api, "customer_a", "Customer_123456")
    token_b = token(api, "customer_b", "Customer_123456")
    assert thread["customer_id"] == api.customers["a"].id
    assert thread["customer_name"] == api.customers["a"].name
    assert thread["assigned_sales_name"] == api.users["sales_a"].display_name
    injected = api.post(
        "/api/inbox/threads",
        json={"subject": "尝试越权", "customer_id": api.customers["b"].id},
        headers=auth(token_a),
    )
    assert injected.status_code == 422
    assert api.get(f"/api/inbox/threads/{thread['id']}", headers=auth(token_b)).status_code == 404
    assert api.get(f"/api/inbox/threads/{thread['id']}/messages", headers=auth(token_b)).status_code == 404
    assert api.post(f"/api/inbox/threads/{thread['id']}/messages", json={"content": "越权回复"}, headers=auth(token_b)).status_code == 404
    assert api.post(f"/api/inbox/threads/{thread['id']}/read", headers=auth(token_b)).status_code == 404
    assert api.get("/api/inbox/threads?customer_id=999", headers=auth(token_a)).status_code == 403


def test_inbox_sales_scope_and_admin_permissions(api):
    thread_a = create_inbox_thread(api, "customer_a", "Customer_123456")
    thread_b = create_inbox_thread(api, "customer_b", "Customer_123456")
    sales_a_token = token(api, "sales_a", "Sales_123456")
    admin_token = token(api, "admin", "Admin_123456")
    sales_items = api.get("/api/inbox/threads", headers=auth(sales_a_token)).json()["items"]
    assert {item["id"] for item in sales_items} == {thread_a["id"]}
    assert sales_items[0]["customer_name"] == api.customers["a"].name
    assert sales_items[0]["assigned_sales_name"] == api.users["sales_a"].display_name
    assert api.get(f"/api/inbox/threads/{thread_b['id']}", headers=auth(sales_a_token)).status_code == 404
    assert api.post(f"/api/inbox/threads/{thread_b['id']}/messages", json={"content": "越权回复"}, headers=auth(sales_a_token)).status_code == 404
    admin_list = api.get("/api/inbox/threads", headers=auth(admin_token)).json()
    assert admin_list["total"] == 2
    assert {item["customer_name"] for item in admin_list["items"]} == {api.customers["a"].name, api.customers["b"].name}
    sales_options = api.get(f"/api/inbox/threads/{thread_a['id']}/sales-options", headers=auth(admin_token))
    assert sales_options.status_code == 200
    assert sales_options.json() == [{"id": api.users["sales_a"].id, "display_name": api.users["sales_a"].display_name}]
    assert api.get(f"/api/inbox/threads/{thread_a['id']}/sales-options", headers=auth(sales_a_token)).status_code == 403
    assert api.patch(
        f"/api/inbox/threads/{thread_a['id']}",
        json={"assigned_sales_id": api.users["sales_a"].id},
        headers=auth(sales_a_token),
    ).status_code == 403
    customer_token = token(api, "customer_a", "Customer_123456")
    assert api.get(f"/api/inbox/threads/{thread_a['id']}/sales-options", headers=auth(customer_token)).status_code == 403
    updated = api.patch(
        f"/api/inbox/threads/{thread_a['id']}",
        json={"status": "pending", "assigned_sales_id": api.users["sales_a"].id},
        headers=auth(admin_token),
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "pending"
    invalid_owner = api.patch(
        f"/api/inbox/threads/{thread_a['id']}",
        json={"assigned_sales_id": api.users["sales_b"].id},
        headers=auth(admin_token),
    )
    assert invalid_owner.status_code == 422
    assert api.patch(f"/api/inbox/threads/{thread_a['id']}", json={"status": "closed"}, headers=auth(customer_token)).status_code == 403


def test_inbox_unread_count_is_scoped_by_current_user(api):
    thread_a = create_inbox_thread(api, "customer_a", "Customer_123456")
    thread_b = create_inbox_thread(api, "customer_b", "Customer_123456")
    customer_a_token = token(api, "customer_a", "Customer_123456")
    customer_b_token = token(api, "customer_b", "Customer_123456")
    sales_a_token = token(api, "sales_a", "Sales_123456")
    sales_b_token = token(api, "sales_b", "Sales_123456")
    admin_token = token(api, "admin", "Admin_123456")

    assert api.post(f"/api/inbox/threads/{thread_a['id']}/messages", json={"content": "客户 A 消息"}, headers=auth(customer_a_token)).status_code == 201
    assert api.post(f"/api/inbox/threads/{thread_b['id']}/messages", json={"content": "客户 B 消息"}, headers=auth(customer_b_token)).status_code == 201
    assert api.get("/api/inbox/unread-count", headers=auth(sales_a_token)).json()["unread_count"] == 1
    assert api.get("/api/inbox/unread-count", headers=auth(sales_b_token)).json()["unread_count"] == 1
    assert api.get("/api/inbox/unread-count", headers=auth(admin_token)).json()["unread_count"] == 2
    assert api.get("/api/inbox/unread-count", headers=auth(customer_a_token)).json()["unread_count"] == 0

    assert api.post(f"/api/inbox/threads/{thread_a['id']}/read", headers=auth(sales_a_token)).status_code == 200
    assert api.post(f"/api/inbox/threads/{thread_a['id']}/messages", json={"content": "销售 A 回复"}, headers=auth(sales_a_token)).status_code == 201
    assert api.post(f"/api/inbox/threads/{thread_b['id']}/messages", json={"content": "销售 B 回复"}, headers=auth(sales_b_token)).status_code == 201
    assert api.get("/api/inbox/unread-count", headers=auth(customer_a_token)).json()["unread_count"] == 1
    assert api.get("/api/inbox/unread-count", headers=auth(customer_b_token)).json()["unread_count"] == 1
    assert api.get("/api/inbox/unread-count", headers=auth(sales_a_token)).json()["unread_count"] == 0
    assert api.get("/api/inbox/unread-count", headers=auth(admin_token)).json()["unread_count"] == 1


def test_inbox_unread_state_status_and_audit(api):
    thread = create_inbox_thread(api, "customer_a", "Customer_123456")
    customer_token = token(api, "customer_a", "Customer_123456")
    sales_token = token(api, "sales_a", "Sales_123456")
    sent = api.post(
        f"/api/inbox/threads/{thread['id']}/messages",
        json={"content": "请协助确认线路状态"},
        headers=auth(customer_token),
    )
    assert sent.status_code == 201
    staff_view = api.get(f"/api/inbox/threads/{thread['id']}", headers=auth(sales_token)).json()
    assert staff_view["staff_unread_count"] == 1
    assert api.post(f"/api/inbox/threads/{thread['id']}/read", headers=auth(sales_token)).json()["updated"] == 1
    reply = api.post(
        f"/api/inbox/threads/{thread['id']}/messages",
        json={"content": "已收到，正在核查"},
        headers=auth(sales_token),
    )
    assert reply.status_code == 201
    customer_view = api.get(f"/api/inbox/threads/{thread['id']}", headers=auth(customer_token)).json()
    assert customer_view["customer_unread_count"] == 1
    assert api.post(f"/api/inbox/threads/{thread['id']}/read", headers=auth(customer_token)).json()["updated"] == 1
    closed = api.patch(f"/api/inbox/threads/{thread['id']}", json={"status": "closed"}, headers=auth(sales_token))
    assert closed.status_code == 200
    assert api.post(f"/api/inbox/threads/{thread['id']}/messages", json={"content": "关闭后发送"}, headers=auth(customer_token)).status_code == 409
    assert api.patch(f"/api/inbox/threads/{thread['id']}", json={"status": "open"}, headers=auth(sales_token)).status_code == 200
    api.db.expire_all()
    stored_messages = api.db.scalars(select(InboxMessage).where(InboxMessage.thread_id == thread["id"]).order_by(InboxMessage.id)).all()
    assert all(message.customer_read_at is not None and message.staff_read_at is not None for message in stored_messages)
    actions = set(api.db.scalars(select(AuditLog.action).where(AuditLog.resource_type == "inbox_thread", AuditLog.resource_id == str(thread["id"]))).all())
    assert {"create", "send_message", "mark_read", "update"}.issubset(actions)


def test_inbox_filters_pagination_and_related_resource_scope(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    sales_token = token(api, "sales_a", "Sales_123456")
    line = api.db.scalar(select(CustomerLine).where(CustomerLine.customer_id == api.customers["a"].id))
    order = api.post(
        "/api/customer/orders",
        json={"product_id": api.products["hk"].id, "quantity": 1, "billing_days": 30},
        headers=auth(customer_token),
    ).json()
    linked = create_inbox_thread(
        api,
        "customer_a",
        "Customer_123456",
        subject="订单交付咨询",
        related_order_id=order["id"],
        related_line_id=line.id,
    )
    create_inbox_thread(api, "customer_a", "Customer_123456", subject="普通咨询")
    create_inbox_thread(api, "customer_b", "Customer_123456", subject="其他销售客户咨询")
    assert api.post(
        f"/api/inbox/threads/{linked['id']}/messages",
        json={"content": "唯一筛选词 alpha-case"},
        headers=auth(customer_token),
    ).status_code == 201
    filtered = api.get(
        f"/api/inbox/threads?order_id={order['id']}&line_id={line.id}&keyword=alpha-case&page=1&page_size=1",
        headers=auth(sales_token),
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == linked["id"]
    paged = api.get("/api/inbox/threads?page=1&page_size=1", headers=auth(sales_token)).json()
    assert paged["total"] == 2
    assert len(paged["items"]) == 1
    admin_paged = api.get("/api/inbox/threads?page=2&page_size=1", headers=auth(token(api, "admin", "Admin_123456"))).json()
    assert admin_paged["total"] == 3
    assert len(admin_paged["items"]) == 1
    assert api.get(
        f"/api/inbox/threads?customer_id={api.customers['b'].id}",
        headers=auth(sales_token),
    ).json()["total"] == 0
    invalid_order = api.post(
        "/api/inbox/threads",
        json={"subject": "越权关联", "related_order_id": order["id"]},
        headers=auth(token(api, "customer_b", "Customer_123456")),
    )
    assert invalid_order.status_code == 422


def test_inbox_rejects_sensitive_content_and_notifications_are_generic(api):
    thread = create_inbox_thread(api, "customer_a", "Customer_123456")
    customer_token = token(api, "customer_a", "Customer_123456")
    sensitive_messages = [
        "password=Secret_123",
        "token: abcdef123456",
        "vless://credential@example",
        "节点地址 10.0.0.1:443",
        "订阅地址 https://example.com/subscription",
    ]
    for content in sensitive_messages:
        response = api.post(f"/api/inbox/threads/{thread['id']}/messages", json={"content": content}, headers=auth(customer_token))
        assert response.status_code == 422
    safe = api.post(
        f"/api/inbox/threads/{thread['id']}/messages",
        json={"content": "请检查我的线路"},
        headers=auth(customer_token),
    )
    assert safe.status_code == 201
    api.db.expire_all()
    notifications = api.db.scalars(select(Notification).where(Notification.category == "inbox")).all()
    assert notifications
    assert all("检查我的线路" not in item.content for item in notifications)
