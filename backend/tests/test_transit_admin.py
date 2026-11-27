"""中转机后台接口：凭据只写不读、连通性测试只读、待配中转可重试交付。"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import transit_admin_routes
from app.core.config import get_settings
from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import (
    Customer,
    CustomerLine,
    Order,
    Product,
    Region,
    TransitProvision,
    TransitRoute,
    TransitVps,
    User,
)
from app.models.base import Base
from app.services.transit_credentials import encrypt_transit_secret

PANEL_PASSWORD = "panel-secret-pass"


class FakePanel:
    def __init__(self, *_args, **_kwargs):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True

    def list_inbounds(self):
        return [{"id": 1, "port": 52000, "remark": "美国-1.2.3.4"}]

    def get_xray_template(self):
        return {
            "outbounds": [{"tag": "direct", "protocol": "freedom"},
                          {"tag": "socks-ORD-1-1", "protocol": "socks"}],
            "routing": {"rules": [{"inboundTag": ["inbound-52000"]}]},
        }


@pytest.fixture()
def api(monkeypatch):
    # 凭据加密需要一个 32 字节的 hex key，生产由环境变量提供
    monkeypatch.setattr(
        get_settings(), "supplier_secret_encryption_key",
        SecretStr("00" * 32), raising=False,
    )
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="root", password_hash=hash_password("Admin_123456"),
                 role="admin", display_name="管理员")
    customer_user = User(username="buyer", password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户")
    db.add_all([admin, customer_user])
    db.flush()
    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    product = Product(name="美国 社媒运营专线 30天", product_type="社媒运营专线", price=95,
                      billing_days=30, region_id=region.id, resource_category="social_media",
                      is_active=True, is_in_stock=True)
    customer = Customer(name="上海某跨境公司", email="buyer@example.com", user_id=customer_user.id)
    db.add_all([product, customer])
    db.flush()
    order = Order(order_no="ORD-TR-1", customer_id=customer.id, product_id=product.id,
                  region_id=region.id, region_name="美国", quantity=1, billing_days=30,
                  unit_price=95, total_amount=95, amount=95, order_type="purchase",
                  payment_method="balance", payment_status="paid", status="pending_transit")
    db.add(order)
    db.flush()
    db.add(TransitRoute(country_code="US", group_code="us", is_enabled=True))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield client, db, order
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client: TestClient) -> dict:
    response = client.post("/api/auth/login", json={"username": "root", "password": "Admin_123456"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


VPS_PAYLOAD = {
    "name": "US-01",
    "group_code": "us",
    "base_url": "https://panel.example.com:54321/abc",
    "username": "admin",
    "password": PANEL_PASSWORD,
    "connect_host": "5.6.7.8",
    "reality_private_key": "priv-key-material",
    "reality_public_key": "pub-key-material",
    "reality_short_id": "d816",
    "port_start": 52000,
    "port_end": 52010,
    "max_inbounds": 10,
}


def test_create_vps_never_returns_password(api):
    client, db, _order = api
    headers = login(client)

    response = client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_password"] is True
    assert PANEL_PASSWORD not in response.text

    listing = client.get("/api/admin/transit/vps", headers=headers)
    assert listing.status_code == 200
    assert PANEL_PASSWORD not in listing.text

    record = db.scalar(select(TransitVps))
    assert record.encrypted_password and record.encrypted_password.startswith("v1:")
    # 密文能解回原文，说明 AAD 用的就是机器名
    from app.services.transit_credentials import decrypt_transit_secret
    assert decrypt_transit_secret(record.encrypted_password, scope="transit:US-01:password") == PANEL_PASSWORD


def test_test_endpoint_is_read_only(api, monkeypatch):
    client, _db, _order = api
    headers = login(client)
    client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)

    monkeypatch.setattr(transit_admin_routes, "XuiClient", FakePanel)
    response = client.post("/api/admin/transit/vps/1/test", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["inbound_count"] == 1
    assert body["socks_outbounds"] == 1
    assert body["next_free_port"] == 52001


def test_retry_transit_delivers_configured_line(api):
    client, db, order = api
    headers = login(client)
    client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)
    vps = db.scalar(select(TransitVps))

    secret = encrypt_transit_secret(
        json.dumps({"ip": "1.2.3.4", "port": 1080, "username": "u1", "password": "p1"}),
        scope=f"transit-exit:{order.id}:1",
    )
    db.add(TransitProvision(
        order_id=order.id, line_index=1, transit_vps_id=vps.id, status="configured",
        inbound_id=7, inbound_port=52000, uuid="uuid-1", outbound_tag="socks-ORD-TR-1-1",
        exit_ip="1.2.3.4", remark="美国-1.2.3.4",
        node_link="vless://uuid-1@5.6.7.8:52000?security=reality#美国-1.2.3.4",
        encrypted_exit_credentials=secret,
    ))
    db.commit()

    response = client.post(f"/api/admin/orders/{order.id}/retry-transit", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "delivered"

    db.expire_all()
    line = db.scalar(select(CustomerLine).where(CustomerLine.order_id == order.id))
    assert line is not None and line.node_link.startswith("vless://")
    refreshed = db.get(Order, order.id)
    assert refreshed.status == "delivered"
    provision = db.scalar(select(TransitProvision))
    assert provision.line_id == line.id


def test_retry_only_for_pending_transit(api):
    client, db, order = api
    headers = login(client)
    order.status = "delivered"
    db.commit()

    response = client.post(f"/api/admin/orders/{order.id}/retry-transit", headers=headers)
    assert response.status_code == 409


def test_provisions_list_shows_pending_transit(api):
    client, db, order = api
    headers = login(client)
    client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)
    vps = db.scalar(select(TransitVps))
    db.add(TransitProvision(
        order_id=order.id, line_index=1, transit_vps_id=vps.id, status="inbound_created",
        inbound_id=9, inbound_port=52001, exit_ip="1.2.3.5", remark="美国-1.2.3.5",
        error_code="XUI_SAVE_TEMPLATE", error_message="面板返回 500",
    ))
    db.commit()

    response = client.get("/api/admin/transit/provisions?status=inbound_created", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["order_no"] == order.order_no
    assert body[0]["vps_name"] == "US-01"
    assert body[0]["error_code"] == "XUI_SAVE_TEMPLATE"
    # 面板凭据绝不能出现在中转记录里
    assert "panel-secret-pass" not in response.text


def test_summary_reports_auto_procurement_state(api):
    client, _db, _order = api
    headers = login(client)
    client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)

    response = client.get("/api/admin/transit/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["panels"] == 1
    assert body["enabled_panels"] == 1
    # 订单 fixture 就是 pending_transit，页面靠这个数提醒运营去处理
    assert body["pending_transit_orders"] == 1
    assert "auto_procurement_enabled" in body


def test_disable_panel_reports_used_inbounds(api):
    """停用前要看得到影响面：这台上已经挂了多少条线路。"""
    client, db, order = api
    headers = login(client)
    client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)
    vps = db.scalar(select(TransitVps))
    db.add(TransitProvision(order_id=order.id, line_index=1, transit_vps_id=vps.id,
                            status="configured", inbound_port=52000))
    db.commit()

    listing = client.get("/api/admin/transit/vps", headers=headers).json()
    assert listing[0]["used_inbounds"] == 1

    # 停用只改开关，已经在这台上的线路记录不动
    response = client.patch("/api/admin/transit/vps/1", json={"is_enabled": False}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_enabled"] is False
    assert body["used_inbounds"] == 1


def test_patch_with_unknown_field_gets_chinese_hint(api):
    """编辑面板多传了 name（后端不支持改名）：提示要能看懂，而不是那句 Extra inputs。"""
    client, _db, _order = api
    headers = login(client)
    client.post("/api/admin/transit/vps", json=VPS_PAYLOAD, headers=headers)

    response = client.patch("/api/admin/transit/vps/1",
                            json={"name": "US-02", "is_enabled": True}, headers=headers)
    assert response.status_code == 422
    assert response.json()["detail"] == "提交了不支持的字段：name"


def test_retry_all_reports_each_order_result(api):
    """批量重试：一单失败不影响其它单，失败原因要逐单返回。

    这个 fixture 里没有任何启用中的面板，所以每单都应该"失败但被记录下来"，
    而不是整批 500——运营需要看到"哪一单为什么没配起来"。
    """
    client, _db, order = api
    headers = login(client)

    response = client.post("/api/admin/transit/provisions/retry-all",
                           params={"limit": 5}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempted"] >= 1
    assert body["failed"] == body["attempted"]
    first = next(item for item in body["results"] if item["order_id"] == order.id)
    assert first["ok"] is False
    assert "中转机" in first["error"] or "中转" in first["error"]
