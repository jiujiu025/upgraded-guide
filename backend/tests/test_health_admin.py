"""管理员线路健康：所有客户线路可按销售/客户/产品/国家筛选，并能批量检测。

真实数据的关键差别：同一台 Node 可能卖给多个客户，所以列表必须从 CustomerLine
出发，否则页面上只剩"一台机器"，看不出这条线是谁的、哪个销售在跟。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import (
    Customer, CustomerLine, Node, Order, Product, Region, TransitProvision, TransitVps, User,
)
from app.models.base import Base


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="root", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售甲")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售乙")
    db.add_all([admin, sales_a, sales_b])
    db.flush()

    us = Region(name="美国", country_code="US")
    vn = Region(name="越南", country_code="VN")
    db.add_all([us, vn])
    db.flush()

    home_us = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=Decimal("60"),
                      billing_days=30, region_id=us.id, resource_category="static_residential",
                      is_active=True, is_in_stock=True)
    social_vn = Product(name="越南 社媒运营专线 30天", product_type="社媒运营专线", price=Decimal("115"),
                        billing_days=30, region_id=vn.id, resource_category="social_media",
                        is_active=True, is_in_stock=True)
    db.add_all([home_us, social_vn])
    db.flush()

    # 同一个出口卖给两个客户：这正是 Node 不够用、必须按 CustomerLine 列的原因
    node = Node(name="美国静态-1", server="10.0.0.1", port=1080, protocol="socks5",
                region_id=us.id, health_status="healthy", health_latency_ms=42,
                health_checked_at=datetime(2026, 11, 26, 1, 0, 0))
    node_down = Node(name="越南专线-1", server="10.0.0.2", port=52000, protocol="vless",
                     region_id=vn.id, health_status="offline")
    node_new = Node(name="美国静态-2", server="10.0.0.3", port=1081, protocol="socks5",
                    region_id=us.id, health_status="unknown")
    db.add_all([node, node_down, node_new])
    db.flush()

    cust_a = Customer(name="杭州甲公司", email="a@test.com", sales_id=sales_a.id)
    cust_b = Customer(name="深圳乙公司", email="b@test.com", sales_id=sales_b.id)
    cust_c = Customer(name="上海丙公司", email="c@test.com", sales_id=None)
    db.add_all([cust_a, cust_b, cust_c])
    db.flush()

    expires = datetime(2026, 12, 26, 0, 0, 0)
    started = datetime(2026, 11, 26, 0, 0, 0)
    order_a = Order(order_no="ORD-A-1", customer_id=cust_a.id, product_id=home_us.id,
                    region_id=us.id, region_name="美国", quantity=1, billing_days=30,
                    unit_price=Decimal("60"), total_amount=Decimal("60"), amount=Decimal("60"),
                    payment_status="paid", status="delivered", order_type="purchase")
    order_b = Order(order_no="ORD-B-1", customer_id=cust_b.id, product_id=social_vn.id,
                    region_id=vn.id, region_name="越南", quantity=1, billing_days=30,
                    unit_price=Decimal("115"), total_amount=Decimal("115"), amount=Decimal("115"),
                    payment_status="paid", status="delivered", order_type="purchase")
    db.add_all([order_a, order_b])
    db.flush()

    line_a = CustomerLine(customer_id=cust_a.id, node_id=node.id, product_id=home_us.id,
                          order_id=order_a.id, static_ip="1.2.3.4", started_at=started,
                          expires_at=expires, status="active",
                          node_link="socks5://u:p@10.0.0.1:1080#美国")
    line_b = CustomerLine(customer_id=cust_b.id, node_id=node_down.id, product_id=social_vn.id,
                          order_id=order_b.id, static_ip="5.6.7.8", started_at=started,
                          expires_at=expires, status="active",
                          node_link="vless://uuid@10.0.0.2:52000#越南-5.6.7.8")
    line_c = CustomerLine(customer_id=cust_c.id, node_id=node_new.id, product_id=home_us.id,
                          started_at=started, expires_at=expires, status="active")
    db.add_all([line_a, line_b, line_c])
    db.flush()

    vps = TransitVps(name="HK-01", group_code="hk", base_url="https://panel.example.com/abc",
                     username="admin", connect_host="9.9.9.9",
                     reality_private_key=None, reality_public_key=None, reality_short_id=None,
                     sni="yahoo.com", dest="yahoo.com:443", fingerprint="random",
                     port_start=52000, port_end=52999, max_inbounds=200, is_enabled=True)
    db.add(vps)
    db.flush()
    db.add(TransitProvision(order_id=order_b.id, line_index=1, transit_vps_id=vps.id,
                            status="configured", inbound_id=7, inbound_port=52000,
                            uuid="uuid-b", outbound_tag="socks-ORD-B-1", exit_ip="5.6.7.8",
                            remark="越南-5.6.7.8", line_id=line_b.id))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client: TestClient, username="root", password="Admin_123456") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_overview_counts_lines_not_nodes(api):
    headers = login(api)
    response = api.get("/api/admin/health/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    # 三条客户线路；node 只有三个但其中一台卖给两个客户时数量会对不上
    assert body["lines"]["total"] == 3
    assert body["lines"]["healthy"] == 1
    assert body["lines"]["offline"] == 1
    assert body["lines"]["unknown"] == 1
    assert body["average_latency_ms"] == 42
    assert body["panels"]["total"] == 1


def test_customer_lines_show_owner_and_entry(api):
    headers = login(api)
    body = api.get("/api/admin/health/customer-lines", headers=headers).json()

    assert body["total"] == 3
    row = next(item for item in body["items"] if item["order_no"] == "ORD-B-1")
    assert row["customer_name"] == "深圳乙公司"
    assert row["sales_name"] == "销售乙"
    assert row["line_code"] == "social"
    assert row["line_label"] == "社媒运营专线"
    # 入口是客户连的中转，出口是住宅 IP——两个都要能看出来
    assert row["entry"] == "10.0.0.2:52000"
    assert row["exit_ip"] == "5.6.7.8"
    assert row["outbound_tag"] == "socks-ORD-B-1"
    assert row["vps_name"] == "HK-01"
    assert body["options"]["sales"][0]["name"] in {"销售甲", "销售乙"}


def test_filter_by_sales_and_keyword(api):
    headers = login(api)
    sales_id = api.get("/api/admin/health/customer-lines", headers=headers).json()["options"]["sales"]
    target = next(item for item in sales_id if item["name"] == "销售甲")

    by_sales = api.get("/api/admin/health/customer-lines",
                       params={"sales_id": target["id"]}, headers=headers).json()
    assert by_sales["total"] == 1
    assert by_sales["items"][0]["customer_name"] == "杭州甲公司"

    by_keyword = api.get("/api/admin/health/customer-lines",
                         params={"keyword": "ORD-B"}, headers=headers).json()
    assert by_keyword["total"] == 1
    assert by_keyword["items"][0]["order_no"] == "ORD-B-1"

    by_ip = api.get("/api/admin/health/customer-lines",
                    params={"keyword": "1.2.3.4"}, headers=headers).json()
    assert by_ip["total"] == 1


def test_filter_by_line_and_health_keeps_counts(api):
    headers = login(api)
    social = api.get("/api/admin/health/customer-lines",
                     params={"line_code": "social"}, headers=headers).json()
    assert social["total"] == 1

    offline = api.get("/api/admin/health/customer-lines",
                      params={"health": "offline"}, headers=headers).json()
    assert offline["total"] == 1
    # 计数不能被 health 筛选收缩，否则下拉框只剩一个选项
    assert offline["counts"]["healthy"] == 1
    assert offline["counts"]["offline"] == 1


def test_batch_check_updates_node_status(api, monkeypatch):
    from app.api import health_admin_routes

    headers = login(api)
    # 10.0.0.2（离线）这轮探测成功，10.0.0.3 仍然不通
    def fake_probe(host, port, timeout=3.0):
        return (True, 15, None) if host == "10.0.0.2" else (False, None, "timeout")

    monkeypatch.setattr(health_admin_routes, "probe", fake_probe)

    response = api.post("/api/admin/health/customer-lines/check", json={"all": True}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checked"] == 3
    assert body["healthy"] == 1
    assert body["offline"] == 2

    offline = api.get("/api/admin/health/customer-lines",
                      params={"health": "offline"}, headers=headers).json()
    assert offline["total"] == 2


def test_transit_inbounds_match_customer(api, monkeypatch):
    from app.api import health_admin_routes

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def list_inbounds(self):
            return [
                {"id": 7, "port": 52000, "remark": "越南-5.6.7.8", "protocol": "vless",
                 "enable": True, "tag": "inbound-52000",
                 "settings": '{"clients":[{"id":"uuid-b","email":"ORD-B-1-1"}]}',
                 "streamSettings": '{"security":"reality","realitySettings":{"serverNames":["yahoo.com"]}}'},
                {"id": 8, "port": 52001, "remark": "手工建的", "protocol": "vless",
                 "enable": True, "tag": "inbound-52001",
                 "settings": '{"clients":[{"id":"uuid-x","email":"manual"}]}',
                 "streamSettings": '{"security":"reality","realitySettings":{"serverNames":["yahoo.com"]}}'},
            ]

        def get_xray_template(self):
            return {
                "outbounds": [
                    {"tag": "direct", "protocol": "freedom"},
                    {"tag": "socks-ORD-B-1", "protocol": "socks",
                     "settings": {"servers": [{"address": "5.6.7.8", "port": 1080}]}},
                ],
                "routing": {"rules": [
                    {"type": "field", "inboundTag": ["inbound-52000"], "outboundTag": "socks-ORD-B-1"},
                ]},
            }

    monkeypatch.setattr(health_admin_routes, "XuiClient", FakeClient)
    headers = login(api)
    response = api.get("/api/admin/health/transit-inbounds", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["items"]) == 2
    matched = next(item for item in body["items"] if item["inbound_id"] == 7)
    assert matched["outbound_tag"] == "socks-ORD-B-1"
    assert matched["exit_host"] == "5.6.7.8"
    assert matched["matched"]["customer_name"] == "深圳乙公司"

    # 手工建的入站没有系统记录，匹配为空但不能报错
    manual = next(item for item in body["items"] if item["inbound_id"] == 8)
    assert manual["matched"] is None
    assert manual["outbound_tag"] is None
