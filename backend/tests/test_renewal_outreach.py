"""到期触达：清单口径、文案、留痕与越权。

这组用例盯的都是"会悄悄错"的地方：

1. 清单必须包含**刚过期**的线路——现实中最该打电话的就是昨天断掉的那批；
2. 销售只能看到自己的客户，也不能给别人的线路留痕；
3. 取不到价目表时报文里不能出现金额，宁可少一句也不能报错价；
4. 留痕存的是当时的文案和剩余天数快照，线路续费后历史不能跟着变。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import (
    Customer, CustomerLine, Node, Product, Region, RetailPriceRule, RenewalOutreach, User,
)
from app.models.base import Base, utcnow
from app.services.registration_service import registration_rate_limiter
from app.services.renewal_outreach_service import outreach_queue

# 以真实当前时间为基准：之前写死 2026-11-08，随着日历推移 "还剩 N 天" 的断言
# 必然失败（跑一次少一天），属于定时炸弹式的测试。
NOW = utcnow()


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # 与生产一致：autoflush=False，查询不触发半成品写入。
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = Session()

    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    node = Node(name="美国-LA01", server="10.0.0.1", port=443, protocol="ss", region_id=region.id)
    # 老式固定价商品：没有资源分类，走 product.price 派生
    legacy = Product(name="美国静态住宅", product_type="独享静态住宅（普通）", price=60, billing_days=30, region_id=region.id)
    # 新式价目表商品：category + 国家 → 月价规则
    priced = Product(name="美国原生独享", product_type="原生独享", price=0, billing_days=30,
                     region_id=region.id, resource_category="美国原生独享")
    # 有分类但没配价：用来验证"取不到价不出金额"
    unpriced = Product(name="英国原生独享", product_type="原生独享", price=0, billing_days=30,
                       region_id=region.id, resource_category="英国原生独享")
    sales = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售甲")
    other = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售乙")
    db.add_all([node, legacy, priced, unpriced, sales, other])
    db.flush()
    db.add(RetailPriceRule(resource_category="美国原生独享", country_code="US", billing_days=30,
                           unit_price=Decimal("100.00"), source_version="test"))
    db.add_all([
        Customer(name="客户甲", sales_id=sales.id, phone="13800000001"),
        Customer(name="客户乙", sales_id=other.id),
    ])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.session_factory = Session
    # 限流是进程级的：同一批用例连着登录会撞上 10 次/分钟的登录限制。
    registration_rate_limiter.clear()
    login = client.post("/api/auth/login", json={"username": "sales_a", "password": "Sales_123456"})
    assert login.status_code == 200, login.text
    client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
    yield client
    app.dependency_overrides.clear()
    registration_rate_limiter.clear()
    db.close()
    engine.dispose()


def add_line(client, *, customer_name="客户甲", product_name="美国静态住宅", expires_in_days=5, status="active"):
    db = client.session_factory()
    try:
        customer = db.scalar(select(Customer).where(Customer.name == customer_name))
        product = db.scalar(select(Product).where(Product.name == product_name))
        node = db.scalar(select(Node))
        line = CustomerLine(
            customer_id=customer.id, node_id=node.id, product_id=product.id,
            started_at=NOW - timedelta(days=25),
            expires_at=NOW + timedelta(days=expires_in_days),
            status=status, static_ip="1.2.3.4",
        )
        db.add(line)
        db.commit()
        return line.id
    finally:
        db.close()


def queue_of(client, **params):
    response = client.get("/api/sales/renewal-outreach", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_queue_includes_soon_and_recently_expired_but_not_far_or_long_gone(api):
    soon = add_line(api, expires_in_days=5)
    later = add_line(api, expires_in_days=20)
    overdue = add_line(api, expires_in_days=-3)
    far = add_line(api, expires_in_days=90)
    long_gone = add_line(api, expires_in_days=-40)

    body = queue_of(api)
    ids = {item["line_id"]: item for item in body["items"]}
    assert set(ids) == {soon, later, overdue}
    assert far not in ids and long_gone not in ids
    # 已过期排最前
    assert body["items"][0]["line_id"] == overdue
    assert body["items"][0]["bucket"] == "overdue"
    assert ids[soon]["bucket"] == "urgent"
    assert ids[later]["bucket"] == "soon"


def test_queue_excludes_other_sales_customers(api):
    add_line(api, expires_in_days=5)
    add_line(api, customer_name="客户乙", expires_in_days=5)
    body = queue_of(api)
    assert {item["customer_name"] for item in body["items"]} == {"客户甲"}


def test_queue_computes_renewal_price_and_message(api):
    add_line(api, product_name="美国原生独享", expires_in_days=7)
    item = queue_of(api)["items"][0]
    assert item["renewal_price"] == 100.0
    assert item["renewal_days"] == 30
    assert item["price_missing"] is False
    # 文案要能直接发：客户名、线路、到期日、天数、金额都在
    message = item["suggested_message"]
    assert "客户甲" in message
    assert "美国-LA01" in message
    assert "还剩 7 天" in message
    assert "¥100.00" in message


def test_queue_omits_price_when_price_book_is_missing(api):
    add_line(api, product_name="英国原生独享", expires_in_days=5)
    item = queue_of(api)["items"][0]
    assert item["renewal_price"] is None
    assert item["price_missing"] is True
    assert "¥" not in item["suggested_message"]
    assert "确认后发您" in item["suggested_message"]


def test_recording_a_touch_snapshots_message_and_days(api):
    line_id = add_line(api, product_name="美国原生独享", expires_in_days=5)
    before = queue_of(api)["items"][0]

    response = api.post(f"/api/sales/renewal-outreach/{line_id}",
                        json={"channel": "wechat", "outcome": "promised", "note": "说周五打款"})
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["channel_label"] == "微信"
    assert created["outcome_label"] == "已答应续费"
    assert created["days_left"] == 5
    # 没传 message 时用模板生成的原文留痕
    assert created["message"] == before["suggested_message"]

    item = queue_of(api)["items"][0]
    assert item["last_touch"]["outcome"] == "promised"
    assert item["last_touch"]["sales_name"] == "销售甲"
    assert queue_of(api)["summary"]["promised"] == 1
    assert queue_of(api)["summary"]["untouched"] == 0


def test_touch_keeps_the_message_actually_sent(api):
    line_id = add_line(api, expires_in_days=3)
    custom = "王哥，这条线后天到期，我先给您留着，回了就续上"
    response = api.post(f"/api/sales/renewal-outreach/{line_id}",
                        json={"channel": "phone", "outcome": "sent", "message": custom})
    assert response.status_code == 201, response.text
    assert response.json()["message"] == custom


def test_cannot_touch_another_sales_line(api):
    add_line(api, customer_name="客户乙", expires_in_days=5)
    db = api.session_factory()
    try:
        foreign = db.scalar(
            select(CustomerLine).join(Customer, CustomerLine.customer_id == Customer.id).where(Customer.name == "客户乙")
        )
        foreign_id = foreign.id
    finally:
        db.close()

    response = api.post(f"/api/sales/renewal-outreach/{foreign_id}",
                        json={"channel": "wechat", "outcome": "sent"})
    assert response.status_code == 403
    db = api.session_factory()
    try:
        assert db.scalars(select(RenewalOutreach)).all() == []
    finally:
        db.close()


def test_unknown_channel_or_outcome_is_rejected(api):
    line_id = add_line(api, expires_in_days=5)
    bad_channel = api.post(f"/api/sales/renewal-outreach/{line_id}",
                           json={"channel": "fax", "outcome": "sent"})
    assert bad_channel.status_code == 422
    bad_outcome = api.post(f"/api/sales/renewal-outreach/{line_id}",
                           json={"channel": "wechat", "outcome": "vibes"})
    assert bad_outcome.status_code == 422


def test_history_is_scoped_and_filterable(api):
    first = add_line(api, expires_in_days=5)
    second = add_line(api, expires_in_days=6)
    add_line(api, customer_name="客户乙", expires_in_days=5)
    api.post(f"/api/sales/renewal-outreach/{first}", json={"channel": "wechat", "outcome": "sent"})
    api.post(f"/api/sales/renewal-outreach/{second}", json={"channel": "phone", "outcome": "declined"})

    history = api.get("/api/sales/renewal-outreach/history")
    assert history.status_code == 200, history.text
    assert len(history.json()["items"]) == 2
    assert {item["line_id"] for item in history.json()["items"]} == {first, second}

    filtered = api.get("/api/sales/renewal-outreach/history", params={"line_id": first})
    assert [item["line_id"] for item in filtered.json()["items"]] == [first]


def test_message_falls_back_to_product_name_without_line_name():
    """线路没有节点名（刚下单还没分配节点）时，文案不能出现空括号。"""
    from app.services.renewal_outreach_service import build_message

    message = build_message(
        customer_name="客户丙", line_name="", product_name="香港独享 IP",
        expires_at=datetime(2026, 11, 15), days_left=7, price=300.0, billing_days=30,
    )
    assert "香港独享 IP（香港独享 IP）" not in message
    assert "香港独享 IP" in message
    assert "¥300.00" in message


def test_message_handles_expired_and_missing_price():
    from app.services.renewal_outreach_service import build_message

    expired = build_message(
        customer_name="客户丁", line_name="美国-LA02", product_name="美国静态住宅",
        expires_at=datetime(2026, 11, 5), days_left=-3, price=None, billing_days=30,
    )
    assert "已过期 3 天" in expired
    assert "¥" not in expired
