"""客户自助升级 / 销售代客升级的入口与限权。

与 ``test_line_upgrade.py``（管理员入口）同一套口径：
- 只补差价 =（目标单价 − 原单价）× 原周期倍数；
- 到期时间不变；
- 客户只能升自己的线路，销售只能升名下客户的线路，越界一律 404；
- 销售不能替客户动余额，只能「联系经理确认」。
"""
from datetime import datetime, timedelta, timezone
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
from app.models import (
    Customer, CustomerLine, Node, Order, Product, Region, RetailPriceRule, User, WalletAccount,
)
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
    for category, price in (("static_residential", 60), ("social_media", 95),
                            ("live_stream_pc", 1100)):
        db.add(RetailPriceRule(resource_category=category, country_code="US",
                               billing_days=30, unit_price=Decimal(price), source_version="test"))
    products = {
        "static_residential": Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=60,
                                      billing_days=30, region_id=region.id,
                                      resource_category="static_residential",
                                      is_active=True, is_in_stock=True),
        "social_media": Product(name="美国 社媒运营专线 30天", product_type="社媒运营专线", price=95,
                                billing_days=30, region_id=region.id,
                                resource_category="social_media",
                                is_active=True, is_in_stock=True),
        "live_stream_pc": Product(name="美国 直播专线（电脑） 30天", product_type="直播专线（电脑）",
                                  price=1100, billing_days=30, region_id=region.id,
                                  resource_category="live_stream_pc",
                                  is_active=True, is_in_stock=True),
    }
    sales_user = User(username="up2_sales", password_hash=hash_password("Sales_123456"),
                      role="sales", display_name="销售")
    customer_user = User(username="up2_cust", password_hash=hash_password("Cust_123456"),
                         role="customer", display_name="客户")
    stranger_user = User(username="up2_stranger", password_hash=hash_password("Cust_123456"),
                         role="customer", display_name="别人")
    db.add_all([*products.values(), sales_user, customer_user, stranger_user])
    db.flush()
    customer = Customer(name="升级客户", email="up2@test.com", user_id=customer_user.id,
                        sales_id=sales_user.id)
    stranger = Customer(name="陌生客户", email="up2b@test.com", user_id=stranger_user.id)
    db.add_all([customer, stranger])
    db.flush()
    # 客户有余额，才能测「付完差价立刻切产品线」这条真实路径
    db.add(WalletAccount(user_id=customer_user.id, balance_cents=200000))

    node = Node(name="us-node", server="10.0.0.9", port=1080, protocol="SOCKS5",
                region_id=region.id, is_enabled=True)
    db.add(node)
    db.flush()
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20)
    line = CustomerLine(customer_id=customer.id, node_id=node.id,
                        product_id=products["static_residential"].id,
                        static_ip="10.0.0.9", started_at=datetime.utcnow(),
                        expires_at=expires, status="active")
    stranger_line = CustomerLine(customer_id=stranger.id, node_id=node.id,
                                 product_id=products["static_residential"].id,
                                 static_ip="10.0.0.10", started_at=datetime.utcnow(),
                                 expires_at=expires, status="active")
    db.add_all([line, stranger_line])
    db.commit()

    def headers_for(user):
        session, _ = create_session(db, user)
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(user.id, user.role, session.id, user.auth_version)}"}

    def override_db():
        session_ = Session()
        try:
            yield session_
        finally:
            session_.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {
        "client": client, "db": db, "line": line, "stranger_line": stranger_line,
        "customer_headers": headers_for(customer_user),
        "stranger_headers": headers_for(stranger_user),
        "sales_headers": headers_for(sales_user),
        "products": products,
    }
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def create_upgrade(api, headers, line_id=None, target="social_media", method="contact_manager"):
    return api["client"].post(
        f"/api/customer/lines/{line_id or api['line'].id}/upgrade",
        headers=headers,
        json={"target_category": target, "payment_method": method,
              "idempotency_key": str(uuid4())},
    )


def create_sales_upgrade(api, headers, target="social_media", method="contact_manager", line_id=None):
    return api["client"].post(
        f"/api/sales/lines/{line_id or api['line'].id}/upgrade",
        headers=headers,
        json={"target_category": target, "payment_method": method,
              "idempotency_key": str(uuid4())},
    )


def test_customer_reads_own_upgrade_options(api):
    response = api["client"].get(f"/api/customer/lines/{api['line'].id}/upgrade-options",
                                 headers=api["customer_headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current"]["category"] == "static_residential"
    # 便宜的在前面：视频/社媒 35，直播 1040
    assert [(item["category"], item["diff"]) for item in body["options"]] == [
        ("social_media", 35.0), ("live_stream_pc", 1040.0),
    ]


def test_customer_cannot_read_others_options(api):
    response = api["client"].get(f"/api/customer/lines/{api['stranger_line'].id}/upgrade-options",
                                 headers=api["customer_headers"])
    assert response.status_code == 404


def test_customer_can_create_upgrade(api):
    response = create_upgrade(api, api["customer_headers"], target="social_media")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["order_type"] == "upgrade"
    assert body["total_amount"] == 35.0
    assert body["billing_days"] == 30
    assert body["status"] == "pending_confirm"
    assert body["created_via"] == "self_service"


def test_customer_cannot_upgrade_others_line(api):
    response = create_upgrade(api, api["customer_headers"], line_id=api["stranger_line"].id)
    assert response.status_code == 404


def test_balance_payment_switches_product_and_keeps_expiry(api):
    """客户自己付差价：付完立即切产品线，到期日一动不动。"""
    db = api["db"]
    expires_before = db.get(CustomerLine, api["line"].id).expires_at
    created = create_upgrade(api, api["customer_headers"], target="social_media", method="balance").json()
    assert created["status"] == "pending_payment"

    response = api["client"].post(f"/api/wallet/orders/{created['id']}/pay-balance",
                                  headers=api["customer_headers"],
                                  json={"idempotency_key": str(uuid4())})
    assert response.status_code == 200, response.text

    db.expire_all()
    line = db.get(CustomerLine, api["line"].id)
    order = db.get(Order, created["id"])
    assert line.product_id == api["products"]["social_media"].id
    assert line.expires_at == expires_before
    assert line.status == "pending_transit"
    assert order.status == "completed"


def test_sales_can_upgrade_customer_line(api):
    response = create_sales_upgrade(api, api["sales_headers"], target="live_stream_pc")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total_amount"] == 1040.0
    assert body["created_via"] == "sales_assisted"


def test_sales_cannot_pay_with_customer_balance(api):
    response = create_sales_upgrade(api, api["sales_headers"], method="balance")
    assert response.status_code == 422


def test_sales_cannot_touch_other_sales_line(api):
    response = create_sales_upgrade(api, api["sales_headers"], line_id=api["stranger_line"].id)
    assert response.status_code == 404
    options = api["client"].get(f"/api/sales/lines/{api['stranger_line'].id}/upgrade-options",
                                headers=api["sales_headers"])
    assert options.status_code == 404


def test_duplicate_pending_upgrade_is_rejected(api):
    assert create_upgrade(api, api["customer_headers"]).status_code == 201
    second = create_upgrade(api, api["customer_headers"], target="live_stream_pc")
    assert second.status_code == 409
    assert "已有待处理" in second.text
