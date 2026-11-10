"""销售工作台接口：订单详情、客户线路、续费订单、代客续费。

设计见 docs/商品上下架与销售端升级设计.md 第 2 节。核心约束：
- 销售只能看/动名下客户的数据，越界一律 404（与"不存在"同一文案，不能用来探测）；
- 代客续费金额与客户自助一致，不另给折扣；
- 客户与管理员调用销售接口一律 403。
"""
from datetime import timedelta
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
from app.models import Customer, CustomerLine, Node, Order, Product, Region, RetailPriceRule, User
from app.models.base import Base, utcnow
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
    # client_lines.node_id 是非空外键，必须提供一个节点
    node = Node(name="美国-01", server="10.0.0.1", port=443, protocol="vless", region_id=region.id)
    db.add(node)
    db.flush()
    db.add(RetailPriceRule(resource_category="static_residential", country_code="US",
                           billing_days=30, unit_price=Decimal("60"), source_version="test"))
    product = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=60,
                      billing_days=30, region_id=region.id, resource_category="static_residential")
    db.add(product)

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 B")
    cust_user = User(username="cust1", password_hash=hash_password("Cust_123456"), role="customer", display_name="客户一号")
    db.add_all([admin, sales_a, sales_b, cust_user])
    db.flush()

    own = Customer(name="A 的客户", email="own@test.com", sales_id=sales_a.id, user_id=cust_user.id)
    other = Customer(name="B 的客户", email="other@test.com", sales_id=sales_b.id)
    db.add_all([own, other])
    db.flush()

    now = utcnow()
    line_own = CustomerLine(customer_id=own.id, product_id=product.id, node_id=node.id, static_ip="10.0.0.1",
                            status="active", started_at=now - timedelta(days=10),
                            expires_at=now + timedelta(days=20))
    line_other = CustomerLine(customer_id=other.id, product_id=product.id, node_id=node.id, static_ip="10.0.0.2",
                              status="active", started_at=now - timedelta(days=5),
                              expires_at=now + timedelta(days=25))
    db.add_all([line_own, line_other])
    db.flush()

    order_own = Order(order_no=f"ORD-T-{uuid4().hex[:6]}", customer_id=own.id, product_id=product.id,
                      amount=60, quantity=1, billing_days=30, unit_price=60, total_amount=60,
                      region_id=region.id, region_name="美国", product_name=product.name,
                      payment_method="contact_manager", payment_status="unpaid",
                      status="pending_payment", assigned_sales_id=sales_a.id, order_type="purchase")
    order_other = Order(order_no=f"ORD-T-{uuid4().hex[:6]}", customer_id=other.id, product_id=product.id,
                        amount=60, quantity=1, billing_days=30, unit_price=60, total_amount=60,
                        region_id=region.id, region_name="美国", product_name=product.name,
                        payment_method="contact_manager", payment_status="unpaid",
                        status="pending_payment", assigned_sales_id=sales_b.id, order_type="purchase")
    db.add_all([order_own, order_other])
    db.commit()

    headers = {}
    for key, user in (("admin", admin), ("sales_a", sales_a), ("sales_b", sales_b), ("customer", cust_user)):
        session, _ = create_session(db, user)
        db.commit()
        headers[key] = {"Authorization": f"Bearer {create_access_token(user.id, user.role, session.id, user.auth_version)}"}

    state = {
        "db": db, "headers": headers,
        "ids": {"line_own": line_own.id, "line_other": line_other.id,
                "order_own": order_own.id, "order_other": order_other.id,
                "customer_own": own.id, "customer_other": other.id, "product": product.id},
    }

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    state["client"] = TestClient(app)
    yield state
    app.dependency_overrides.clear()


def test_sales_reads_own_order_but_not_others(api):
    client, ids, headers = api["client"], api["ids"], api["headers"]
    ok = client.get(f"/api/sales/orders/{ids['order_own']}", headers=headers["sales_a"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["id"] == ids["order_own"]

    # 别人的订单：404 而不是 403，避免被拿来枚举
    other = client.get(f"/api/sales/orders/{ids['order_other']}", headers=headers["sales_a"])
    assert other.status_code == 404
    missing = client.get("/api/sales/orders/999999", headers=headers["sales_a"])
    assert missing.status_code == 404
    assert other.json()["detail"] == missing.json()["detail"]


def test_sales_lines_are_scoped(api):
    client, headers = api["client"], api["headers"]
    body = client.get("/api/sales/lines", headers=headers["sales_a"]).json()
    # customer_line_payload 对外字段名是 ip（不是模型里的 static_ip）
    assert [item["ip"] for item in body] == ["10.0.0.1"]

    # 按客户过滤
    own = client.get(f"/api/sales/lines?customer_id={api['ids']['customer_own']}", headers=headers["sales_a"])
    assert own.status_code == 200 and len(own.json()) == 1

    # 别人的客户 → 404
    cross = client.get(f"/api/sales/lines?customer_id={api['ids']['customer_other']}", headers=headers["sales_a"])
    assert cross.status_code == 404


def test_sales_sees_only_own_renewal_orders(api):
    client, headers = api["client"], api["headers"]
    # 初始没有续费单
    assert client.get("/api/sales/renewals", headers=headers["sales_a"]).json() == []

    created = client.post(f"/api/sales/lines/{api['ids']['line_own']}/renew",
                          json={"billing_days": 30, "payment_method": "contact_manager"},
                          headers=headers["sales_a"])
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["order_type"] == "renewal"
    assert body["total_amount"] == 60
    assert "销售代办" in (body["remark"] or "")

    mine = client.get("/api/sales/renewals", headers=headers["sales_a"]).json()
    assert [item["id"] for item in mine] == [body["id"]]

    # 另一个销售看不到
    assert client.get("/api/sales/renewals", headers=headers["sales_b"]).json() == []


def test_sales_cannot_renew_someone_elses_line(api):
    response = api["client"].post(f"/api/sales/lines/{api['ids']['line_other']}/renew",
                                  json={"billing_days": 30, "payment_method": "contact_manager"},
                                  headers=api["headers"]["sales_a"])
    assert response.status_code == 404


def test_duplicate_renewal_is_rejected(api):
    client, headers = api["client"], api["headers"]["sales_a"]
    line_id = api["ids"]["line_own"]
    first = client.post(f"/api/sales/lines/{line_id}/renew",
                        json={"billing_days": 30, "payment_method": "contact_manager"}, headers=headers)
    assert first.status_code == 200
    second = client.post(f"/api/sales/lines/{line_id}/renew",
                         json={"billing_days": 30, "payment_method": "contact_manager"}, headers=headers)
    assert second.status_code == 409
    assert "进行中的续费订单" in second.json()["detail"]


def test_renewal_uses_the_same_price_as_self_service(api):
    """代客续费不能有第二个价格口径。"""
    client, headers = api["client"], api["headers"]["sales_a"]
    body = client.post(f"/api/sales/lines/{api['ids']['line_own']}/renew",
                       json={"billing_days": 30, "payment_method": "contact_manager"}, headers=headers).json()
    rule = api["db"].scalar(select(RetailPriceRule).where(RetailPriceRule.resource_category == "static_residential"))
    assert body["unit_price"] == float(rule.unit_price)
    assert body["total_amount"] == float(rule.unit_price)


def test_other_roles_are_blocked(api):
    client, ids = api["client"], api["ids"]
    for role in ("customer", "admin"):
        headers = api["headers"][role]
        assert client.get("/api/sales/orders/%d" % ids["order_own"], headers=headers).status_code == 403
        assert client.get("/api/sales/lines", headers=headers).status_code == 403
        assert client.get("/api/sales/renewals", headers=headers).status_code == 403
        assert client.post(f"/api/sales/lines/{ids['line_own']}/renew",
                           json={"billing_days": 30, "payment_method": "contact_manager"},
                           headers=headers).status_code == 403


def test_renewal_survives_a_deactivated_region(api):
    """停用一个地区不能把历史客户的续费一起停掉。

    线上真实情况：老线路的商品挂在后来被合并停用的「香港」上（现在用的是「中国香港」），
    续费时如果还要求地区处于启用状态，这些客户就永远续不了费。
    """
    db = api["db"]
    region = db.scalar(select(Region).where(Region.country_code == "US"))
    region.is_active = False
    db.commit()

    response = api["client"].post(f"/api/sales/lines/{api['ids']['line_own']}/renew",
                                  json={"billing_days": 30, "payment_method": "contact_manager"},
                                  headers=api["headers"]["sales_a"])
    assert response.status_code == 200, response.text
    assert response.json()["order_type"] == "renewal"

    # 但新购不受影响：地区停用后不能下单
    purchase = api["client"].post("/api/orders/on-behalf",
                                  json={"customer_id": api["ids"]["customer_own"],
                                        "product_id": api["ids"]["product"],
                                        "quantity": 1, "billing_days": 30,
                                        "payment_method": "contact_manager",
                                        "idempotency_key": "deactivated-region-test"},
                                  headers=api["headers"]["sales_a"])
    assert purchase.status_code == 404 or purchase.status_code == 422
