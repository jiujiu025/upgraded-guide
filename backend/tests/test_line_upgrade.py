"""线路升级：静态住宅 → 社媒/直播，只补差价，到期时间不变。

业务口径（2026-11-13 玖夜确认）：
- 差价 =（目标单价 − 原单价）× 原订单周期倍数，不按剩余天数折算；
- 到期时间完全不变，升级不重新计时；
- 原住宅 IP 保留，社媒/直播只是多一层中转。

测试重点：
1. 差价算对（美国 30 天：95 − 60 = 35；90 天：35×3 = 105）；
2. 同类型、降级都要拒绝；
3. 收款后线路换产品但 expires_at 不动；
4. 目标需要中转时线路进 pending_transit，不假装已开通。
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
from app.models import Customer, CustomerLine, Node, Order, Product, Region, RetailPriceRule, User
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
    residential = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=60,
                          billing_days=30, region_id=region.id, resource_category="static_residential",
                          is_active=True, is_in_stock=True)
    social = Product(name="美国 社媒运营专线 30天", product_type="社媒运营专线", price=95,
                     billing_days=30, region_id=region.id, resource_category="social_media",
                     is_active=True, is_in_stock=True)
    live = Product(name="美国 直播专线（电脑） 30天", product_type="直播专线（电脑）", price=1100,
                   billing_days=30, region_id=region.id, resource_category="live_stream_pc",
                   is_active=True, is_in_stock=True)
    admin = User(username="up_admin", password_hash=hash_password("Admin_123456"),
                 role="admin", display_name="管理员")
    customer_user = User(username="up_cust", password_hash=hash_password("Cust_123456"),
                         role="customer", display_name="客户")
    db.add_all([residential, social, live, admin, customer_user])
    db.flush()
    customer = Customer(name="升级客户", email="upgrade@test.com", user_id=customer_user.id)
    db.add(customer)
    db.flush()

    node = Node(name="us-node", server="10.0.0.9", port=1080, protocol="SOCKS5",
                region_id=region.id, is_enabled=True)
    db.add(node)
    db.flush()
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20)
    line = CustomerLine(customer_id=customer.id, node_id=node.id, product_id=residential.id,
                        static_ip="10.0.0.9", started_at=datetime.utcnow(),
                        expires_at=expires, status="active")
    db.add(line)
    db.commit()

    session, _ = create_session(db, admin)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, session.id, admin.auth_version)}"}

    def override_db():
        session_ = Session()
        try:
            yield session_
        finally:
            session_.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"client": client, "db": db, "headers": headers, "line": line,
           "products": {"static_residential": residential, "social_media": social,
                        "live_stream_pc": live}}
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def upgrade(api, target="social_media", **overrides):
    body = {"target_category": target, "payment_method": "contact_manager",
            "idempotency_key": str(uuid4())}
    body.update(overrides)
    return api["client"].post(f"/api/admin/lines/{api['line'].id}/upgrade",
                              headers=api["headers"], json=body)


def test_upgrade_charges_only_the_difference(api):
    response = upgrade(api, "social_media")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["order_type"] == "upgrade"
    assert body["total_amount"] == 35.0        # 95 - 60
    assert body["unit_price"] == 35.0
    assert body["billing_days"] == 30
    assert body["status"] == "pending_confirm"
    assert body["renewal_line_id"] == api["line"].id


def test_upgrade_to_live_costs_more(api):
    response = upgrade(api, "live_stream_pc")
    assert response.status_code == 201, response.text
    assert response.json()["total_amount"] == 1040.0   # 1100 - 60


def test_same_category_rejected(api):
    response = upgrade(api, "static_residential")
    assert response.status_code == 409


def test_downgrade_rejected(api):
    """线路已经是社媒时不能"升级"回静态住宅。"""
    db = api["db"]
    line = db.get(CustomerLine, api["line"].id)
    line.product_id = api["products"]["social_media"].id
    db.commit()

    response = upgrade(api, "static_residential")
    assert response.status_code == 409
    assert "单价更高" in response.text


def test_confirm_switches_product_and_keeps_expiry(api):
    db = api["db"]
    line = db.get(CustomerLine, api["line"].id)
    expires_before = line.expires_at

    created = upgrade(api, "social_media").json()
    response = api["client"].post(f"/api/admin/orders/{created['id']}/confirm",
                                  headers=api["headers"])
    assert response.status_code == 200, response.text

    db.expire_all()
    line = db.get(CustomerLine, api["line"].id)
    assert line.product_id == api["products"]["social_media"].id
    # 到期时间必须一模一样，升级不重新计时
    assert line.expires_at == expires_before
    # 社媒需要中转，没配好之前不能标成已开通
    assert line.status == "pending_transit"

    order = db.get(Order, created["id"])
    assert order.status == "completed"
    assert order.payment_status == "manually_confirmed"


def test_upgrade_is_idempotent(api):
    key = str(uuid4())
    first = upgrade(api, "social_media", idempotency_key=key)
    second = upgrade(api, "social_media", idempotency_key=key)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    db = api["db"]
    assert len(db.scalars(select(Order).where(Order.order_type == "upgrade")).all()) == 1


def test_upgrade_options_only_list_positive_diffs(api):
    """候选只列“更贵的”，并且差价与下单口径一致。"""
    response = api["client"].get(f"/api/admin/lines/{api['line'].id}/upgrade-options",
                                 headers=api["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current"]["category"] == "static_residential"
    assert body["current"]["unit_price"] == 60.0
    diffs = {item["category"]: item["diff"] for item in body["options"]}
    assert diffs["social_media"] == 35.0
    assert diffs["live_stream_pc"] == 1040.0


def test_unpaid_upgrade_cannot_be_confirmed(api):
    """余额支付的升级单是 pending_payment，钱没到不能确认切线路。"""
    created = upgrade(api, "social_media", payment_method="balance").json()
    assert created["status"] == "pending_payment"
    response = api["client"].post(f"/api/admin/orders/{created['id']}/confirm",
                                  headers=api["headers"])
    assert response.status_code == 409
    db = api["db"]
    db.expire_all()
    assert db.get(CustomerLine, api["line"].id).product_id == api["products"]["static_residential"].id


def test_refund_rolls_back_product_line(api):
    """升级单退款必须把线路切回原产品，否则客户既拿回钱又保住升级。"""
    from app.services.order_service import rollback_upgrade

    db = api["db"]
    expires_before = db.get(CustomerLine, api["line"].id).expires_at
    created = upgrade(api, "social_media").json()
    assert api["client"].post(f"/api/admin/orders/{created['id']}/confirm",
                              headers=api["headers"]).status_code == 200

    db.expire_all()
    line = db.get(CustomerLine, api["line"].id)
    assert line.product_id == api["products"]["social_media"].id
    assert line.status == "pending_transit"

    order = db.get(Order, created["id"])
    restored = rollback_upgrade(db, order)
    db.commit()
    assert restored is not None
    assert restored.product_id == api["products"]["static_residential"].id
    assert restored.expires_at == expires_before
    # 回退后不再需要中转，状态要跟着回到 active
    assert restored.status == "active"


def test_expired_line_cannot_be_upgraded(api):
    """升级不动到期时间，过期线路升级会得到「active 但已过期」的矛盾数据。"""
    db = api["db"]
    line = db.get(CustomerLine, api["line"].id)
    line.status = "expired"
    db.commit()

    assert upgrade(api, "social_media").status_code == 409


def test_second_pending_upgrade_is_rejected(api):
    """同一线路不能堆多张待处理升级单（双击、网络重试都可能绕过幂等键）。"""
    assert upgrade(api, "social_media").status_code == 201
    response = upgrade(api, "live_stream_pc")
    assert response.status_code == 409
    assert "已有待处理" in response.text


def test_pending_transit_order_cannot_be_cancelled(api):
    """已付款且采购了 IP 的待配中转单，不能当普通单取消。"""
    db = api["db"]
    created = upgrade(api, "social_media").json()
    order = db.get(Order, created["id"])
    order.status = "pending_transit"
    db.commit()

    response = api["client"].post(f"/api/admin/orders/{created['id']}/cancel",
                                  headers=api["headers"])
    assert response.status_code == 409
    assert "中转" in response.text
