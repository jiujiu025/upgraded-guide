"""代客下单：权限边界、幂等、折扣（仅管理员，7 折上限）、无账号客户限制。

设计见 docs/销售代客下单设计.md。核心约束：
- 销售只能给名下客户开单，别人的客户一律 404（不泄露存在性）；
- 折扣只有管理员能设，上限由 MAX_ORDER_DISCOUNT_PERCENT 控制（默认 30% = 7 折）；
- 幂等键必填：同键同客户返回原单，同键不同客户 409；
- 客户没有登录账号时只能用「联系经理」方式。
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import AuditLog, Customer, Notification, Order, Product, Region, RetailPriceRule, User
from app.models.base import Base
from app.services.session_service import create_session


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    db.add(RetailPriceRule(resource_category="static_residential", country_code="US",
                           billing_days=30, unit_price=Decimal("60"), source_version="test"))
    product = Product(name="美国 独享静态住宅 30天", product_type="独享静态住宅（普通）", price=60,
                      billing_days=30, region_id=region.id, resource_category="static_residential")
    disabled_product = Product(name="已下架产品", product_type="独享静态住宅（普通）", price=60,
                               billing_days=30, region_id=region.id, resource_category="static_residential",
                               is_active=False)
    db.add_all([product, disabled_product])

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 B")
    customer_user = User(username="cust1", password_hash=hash_password("Cust_123456"), role="customer", display_name="客户一号")
    db.add_all([admin, sales_a, sales_b, customer_user])
    db.flush()

    own = Customer(name="A 的客户", email="own@test.com", sales_id=sales_a.id, user_id=customer_user.id)
    other = Customer(name="B 的客户", email="other@test.com", sales_id=sales_b.id)
    no_account = Customer(name="无账号客户", email="noacct@test.com", sales_id=sales_a.id)
    deleted = Customer(name="已删除客户", email="gone@test.com", sales_id=sales_a.id, is_deleted=True)
    db.add_all([own, other, no_account, deleted])
    db.commit()

    headers = {}
    for key, user in (("admin", admin), ("sales_a", sales_a), ("sales_b", sales_b), ("customer", customer_user)):
        session, _ = create_session(db, user)
        db.commit()
        headers[key] = {"Authorization": f"Bearer {create_access_token(user.id, user.role, session.id, user.auth_version)}"}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"db": db, "client": client, "headers": headers, "product": product,
           "disabled": disabled_product, "region": region, "own": own, "other": other,
           "no_account": no_account, "deleted": deleted, "sales_a": sales_a, "admin": admin}

    app.dependency_overrides.clear()


def payload(api, **overrides):
    body = {
        "customer_id": api["own"].id,
        "product_id": api["product"].id,
        "quantity": 1,
        "billing_days": 30,
        "payment_method": "contact_manager",
        "idempotency_key": str(uuid4()),
    }
    body.update(overrides)
    return body


def post(api, who, **overrides):
    return api["client"].post("/api/orders/on-behalf", json=payload(api, **overrides), headers=api["headers"][who])


def test_sales_can_order_for_own_customer_and_attribution_is_recorded(api):
    response = post(api, "sales_a")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_via"] == "sales_assisted"
    assert body["created_by_user_id"] == api["sales_a"].id
    assert body["assigned_sales_id"] == api["sales_a"].id
    assert body["total_amount"] == 60.0
    # 客户收到通知，销售本人不额外收一条（自己刚点的，站内信是噪音）
    notifications = api["db"].scalars(select(Notification).where(Notification.user_id == api["sales_a"].id)).all()
    assert notifications == []
    customer_notices = api["db"].scalars(select(Notification).where(Notification.title == "订单已创建")).all()
    assert len(customer_notices) == 1


def test_sales_cannot_order_for_another_sales_customer(api):
    response = post(api, "sales_a", customer_id=api["other"].id)
    assert response.status_code == 404
    # 不泄露"客户存在但无权"，与不存在的客户返回同一条文案
    missing = post(api, "sales_a", customer_id=999999)
    assert missing.status_code == 404
    assert missing.json()["detail"] == response.json()["detail"]


def test_admin_can_order_for_any_customer(api):
    response = post(api, "admin", customer_id=api["other"].id)
    assert response.status_code == 200, response.text
    assert response.json()["created_via"] == "admin"


def test_customer_cannot_use_on_behalf_endpoint(api):
    response = post(api, "customer")
    assert response.status_code == 403


def test_deleted_customer_is_rejected(api):
    assert post(api, "admin", customer_id=api["deleted"].id).status_code == 404


def test_disabled_product_is_rejected(api):
    assert post(api, "sales_a", product_id=api["disabled"].id).status_code == 404


def test_idempotency_key_returns_same_order(api):
    key = str(uuid4())
    first = post(api, "sales_a", idempotency_key=key)
    second = post(api, "sales_a", idempotency_key=key)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(api["db"].scalars(select(Order)).all()) == 1


def test_idempotency_key_reused_for_other_customer_conflicts(api):
    key = str(uuid4())
    assert post(api, "admin", idempotency_key=key).status_code == 200
    conflict = post(api, "admin", customer_id=api["other"].id, idempotency_key=key)
    assert conflict.status_code == 409


def test_customer_without_account_is_limited_to_contact_manager(api):
    assert post(api, "sales_a", customer_id=api["no_account"].id, payment_method="online").status_code == 422
    assert post(api, "sales_a", customer_id=api["no_account"].id, payment_method="balance").status_code == 422
    ok = post(api, "sales_a", customer_id=api["no_account"].id, payment_method="contact_manager")
    assert ok.status_code == 200
    assert ok.json()["status"] == "pending_confirm"


def test_online_payment_order_enters_pending_payment_for_account_holder(api):
    response = post(api, "sales_a", payment_method="online")
    assert response.status_code == 200
    assert response.json()["status"] == "pending_payment"


def test_discount_is_admin_only(api):
    response = post(api, "sales_a", discount_percent=10)
    assert response.status_code == 403
    assert "管理员" in response.json()["detail"]


def test_discount_applies_at_seven_tenths(api):
    response = post(api, "admin", discount_percent=30)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unit_price"] == 42.0          # 60 × 0.7
    assert body["total_amount"] == 42.0
    assert "管理员折扣 30%" in (body["remark"] or "")


def test_discount_above_limit_is_rejected(api):
    response = post(api, "admin", discount_percent=31)
    assert response.status_code == 422
    assert "上限" in response.json()["detail"]


def test_discount_keeps_total_consistent_with_quantity(api):
    response = post(api, "admin", discount_percent=30, quantity=3)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unit_price"] == 42.0
    assert body["total_amount"] == 126.0       # 42 × 3，不是 180 × 0.7 的浮点残差


def test_on_behalf_order_is_audited(api):
    response = post(api, "sales_a")
    assert response.status_code == 200
    order_id = response.json()["id"]
    audits = api["db"].scalars(select(AuditLog).where(AuditLog.resource_type == "order",
                                                      AuditLog.resource_id == str(order_id))).all()
    assert any("代客下单" in (entry.detail or "") for entry in audits)


def test_idempotency_key_is_required(api):
    body = payload(api)
    body.pop("idempotency_key")
    response = api["client"].post("/api/orders/on-behalf", json=body, headers=api["headers"]["sales_a"])
    assert response.status_code == 422


def test_customer_self_orders_still_work(api):
    """客户自助下单不受影响，且 created_via 落 self_service。"""
    response = api["client"].post(
        "/api/customer/orders",
        json={"product_id": api["product"].id, "region_id": api["region"].id, "quantity": 1, "billing_days": 30},
        headers=api["headers"]["customer"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_via"] == "self_service"
    assert response.json()["assigned_sales_id"] == api["sales_a"].id
