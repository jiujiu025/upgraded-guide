"""交付线路：粘贴分享链接自动识别、生成二维码与 Clash 订阅。

覆盖三块：
1. 链接解析（vless/vmess/trojan/ss/hysteria2/tuic）与 Clash 条目映射；
2. 客户订阅 token 的签发/校验/防篡改，以及 Clash / base64 两种输出；
3. 交付接口贴链接即可，自动建节点、回填订阅地址与二维码地址。
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, CustomerLine, Node, Product, Region, RetailPriceRule, User
from app.models.base import Base, utcnow
from app.services.proxy_link import ProxyLinkError, parse_link, to_clash_proxy, to_share_link
from app.services.subscription_service import (
    build_base64_subscription, build_clash_config, issue_token, parse_token,
)

ADMIN_PASSWORD = "Admin_123456"
CUSTOMER_PASSWORD = "Customer_123456"

VLESS_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@us.example.com:443"
    "?type=ws&security=tls&sni=us.example.com&path=%2Fws&host=us.example.com&fp=chrome"
    "#%E7%BE%8E%E5%9B%BD%E6%B4%9B%E6%9D%89%E7%9F%B6"
)


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    admin = User(username="admin", password_hash=hash_password(ADMIN_PASSWORD), role="admin", display_name="管理员")
    customer_user = User(username="buyer", password_hash=hash_password(CUSTOMER_PASSWORD),
                         role="customer", display_name="客户")
    db.add_all([admin, customer_user])
    db.flush()
    customer = Customer(name="客户", email="buyer@example.com", user_id=customer_user.id)
    product = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=100,
                      billing_days=30, region_id=region.id, is_active=True, is_in_stock=True,
                      resource_category="static_residential")
    db.add_all([customer, product])
    db.add(RetailPriceRule(resource_category="static_residential", country_code="US",
                           billing_days=30, unit_price=Decimal("100"), source_version="test"))
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
    client.product = product
    client.customer = customer
    yield client
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def paid_order(client: TestClient) -> int:
    """走线下付款流程到「已收款待交付」，交付校验的就是这个状态。"""
    customer_headers = login(client, "buyer", CUSTOMER_PASSWORD)
    admin_headers = login(client, "admin", ADMIN_PASSWORD)
    order = client.post("/api/customer/orders", json={
        "product_id": client.product.id, "quantity": 1, "billing_days": 30,
        "payment_method": "contact_manager",
    }, headers=customer_headers)
    assert order.status_code == 200, order.text
    order_id = order.json()["id"]
    assert client.post(f"/api/admin/orders/{order_id}/confirm", headers=admin_headers).status_code == 200
    return order_id


# ---------------------------------------------------------------- 链接解析


def test_parse_supports_main_protocols():
    import base64
    import json

    cases = {
        "vless": VLESS_LINK,
        "trojan": "trojan://pass123@jp.example.com:8443?sni=jp.example.com#日本",
        "ss": "ss://YWVzLTI1Ni1nY206c2VjcmV0@hk.example.com:8388#香港",
        "hysteria2": "hysteria2://letmein@sg.example.com:443?sni=sg.example.com&insecure=1#SG",
        # vmess 的链接体是 base64(JSON)，读起来没有可读性，单独拼
        "vmess": "vmess://" + base64.b64encode(json.dumps({
            "v": "2", "ps": "澳洲", "add": "au.example.com", "port": "8443",
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "aid": "0", "net": "ws",
            "type": "none", "host": "au.example.com", "path": "/ray", "tls": "tls",
        }).encode()).decode(),
    }

    for protocol, link in cases.items():
        node = parse_link(link)
        assert node.protocol == protocol
        assert node.server.endswith("example.com")
        assert node.port in (443, 8388, 8443)
        assert node.credential


def test_parse_rejects_unknown_or_broken_links():
    with pytest.raises(ProxyLinkError):
        parse_link("ssr://abcdef")
    with pytest.raises(ProxyLinkError):
        parse_link("not-a-link")
    with pytest.raises(ProxyLinkError):
        parse_link("vless://@host:443")  # 缺 uuid


def test_clash_proxy_keeps_transport_and_tls_fields():
    proxy = to_clash_proxy(parse_link(VLESS_LINK), "美国-1")
    assert proxy["type"] == "vless"
    assert proxy["network"] == "ws"
    assert proxy["ws-opts"]["path"] == "/ws"
    assert proxy["ws-opts"]["headers"] == {"Host": "us.example.com"}
    assert proxy["tls"] is True and proxy["servername"] == "us.example.com"


def test_share_link_roundtrip():
    node = parse_link(VLESS_LINK)
    again = parse_link(to_share_link(node))
    assert (again.protocol, again.server, again.port, again.credential) == \
           (node.protocol, node.server, node.port, node.credential)


# ---------------------------------------------------------------- 订阅


def test_token_roundtrip_and_tamper():
    token = issue_token(37)
    assert parse_token(token) == 37
    payload, signature = token.split(".", 1)
    assert parse_token(f"{payload}.{signature[:-1]}x") is None
    assert parse_token("bad") is None


def test_clash_config_lists_proxies_and_groups():
    text = build_clash_config([VLESS_LINK])
    assert "proxies:" in text and "type: vless" in text
    assert "节点选择" in text and "MATCH,节点选择" in text
    # 空订阅也要能加载，不能让客户端更新时报错
    assert "DIRECT" in build_clash_config([])


def test_base64_subscription_contains_original_link():
    import base64

    decoded = base64.b64decode(build_base64_subscription([VLESS_LINK])).decode()
    assert decoded.startswith("vless://")


# ---------------------------------------------------------------- 交付


def test_deliver_with_link_creates_node_and_subscription(api):
    order_id = paid_order(api)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    response = api.post(f"/api/admin/orders/{order_id}/deliver", json={
        "node_link": VLESS_LINK,
        "expires_at": (utcnow() + timedelta(days=30)).isoformat(),
    }, headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["node_link"] == VLESS_LINK
    assert "/api/sub/" in body["subscription_url"]
    assert body["node_qr_url"].endswith(".svg")
    assert body["subscription_qr_url"].endswith("/qr.svg")

    # 节点被自动创建并挂到订单地区
    node = api.db.scalar(select(Node).where(Node.server == "us.example.com"))
    assert node is not None and node.protocol == "vless"
    line = api.db.scalar(select(CustomerLine).where(CustomerLine.id == body["id"]))
    assert line.node_id == node.id


def test_deliver_requires_link_or_node(api):
    order_id = paid_order(api)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    response = api.post(f"/api/admin/orders/{order_id}/deliver", json={
        "expires_at": (utcnow() + timedelta(days=30)).isoformat(),
    }, headers=admin_headers)
    assert response.status_code == 422


def test_deliver_rejects_unsupported_protocol(api):
    order_id = paid_order(api)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    response = api.post(f"/api/admin/orders/{order_id}/deliver", json={
        "node_link": "ssr://something",
        "expires_at": (utcnow() + timedelta(days=30)).isoformat(),
    }, headers=admin_headers)
    assert response.status_code == 422
    assert "暂不支持" in response.json()["detail"]


def test_subscription_endpoint_serves_clash_and_base64(api):
    order_id = paid_order(api)
    admin_headers = login(api, "admin", ADMIN_PASSWORD)
    delivered = api.post(f"/api/admin/orders/{order_id}/deliver", json={
        "node_link": VLESS_LINK,
        "expires_at": (utcnow() + timedelta(days=30)).isoformat(),
    }, headers=admin_headers).json()
    token = delivered["subscription_url"].rsplit("/", 1)[-1]

    # Clash 系客户端带 UA 或显式 target=clash
    clash = api.get(f"/api/sub/{token}", headers={"User-Agent": "ClashX/1.0"})
    assert clash.status_code == 200
    assert "proxies:" in clash.text and "us.example.com" in clash.text

    generic = api.get(f"/api/sub/{token}")
    assert generic.status_code == 200
    import base64
    assert base64.b64decode(generic.text).decode().startswith("vless://")

    # 二维码：订阅地址与单条节点各一张
    assert api.get(f"/api/sub/{token}/qr.svg").headers["content-type"].startswith("image/svg+xml")
    node_qr = delivered["node_qr_url"]
    assert api.get(node_qr).status_code == 200


def test_subscription_hides_invalid_token(api):
    assert api.get("/api/sub/1.bad").status_code == 404
