"""中转自动化：入站 / 出站 / 路由 三个对象与幂等。

重点不是"能不能调通面板"，而是：
1. 一条线路只建一个入站，重试不重复建；
2. 出站必须指向真实的住宅出口 IP，路由必须绑到 inbound-{port}；
3. 面板保存失败时入站已存在，状态必须留在 inbound_created 供重试，不能清掉。
"""
import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.services import procurement_service, transit_service
from app.services.transit_service import (
    TransitNotConfigured,
    TransitProvisionError,
    provision_transit,
)
from app.services.xui_client import XuiError
from app.suppliers.base import PurchaseResult


class FakeAccount:
    def __init__(self, ip="1.2.3.4", port=1080, username="u1", password="p1"):
        self.ip = ip
        self.host = ip
        self.port = port
        self.username = username
        self.password = password
        self.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30)


class FakePanel:
    """内存版 3x-ui：只实现被调用的那几个方法。"""

    def __init__(self, fail_save=False):
        self.template = {
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": {"domainStrategy": "AsIs", "rules": []},
        }
        self.inbounds: dict[int, dict] = {}
        self.next_id = 1
        self.added: list[dict] = []
        self.saved = 0
        self.restarts = 0
        self.fail_save = fail_save

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def get_inbound(self, inbound_id):
        return self.inbounds.get(inbound_id)

    def used_ports(self):
        return {item["port"] for item in self.inbounds.values()}

    def add_inbound(self, payload):
        inbound_id = self.next_id
        self.next_id += 1
        stored = dict(payload)
        stored["id"] = inbound_id
        self.inbounds[inbound_id] = stored
        self.added.append(stored)
        return {"id": inbound_id}

    def list_inbounds(self):
        return list(self.inbounds.values())

    def get_xray_template(self):
        return copy.deepcopy(self.template)

    def save_xray_template(self, config):
        if self.fail_save:
            raise XuiError("面板返回 500", code="XUI_SAVE_TEMPLATE", status=500)
        self.template = copy.deepcopy(config)
        self.saved += 1

    def restart_xray(self):
        self.restarts += 1


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    region = Region(name="美国", country_code="US")
    session.add(region)
    session.flush()
    user = User(username="buyer", password_hash="x", role="customer", display_name="买家")
    session.add(user)
    session.flush()
    customer = Customer(name="买家", email="buyer@example.com", user_id=user.id)
    product = Product(name="美国 社媒运营专线 30天", product_type="社媒运营专线", price=95,
                      billing_days=30, region_id=region.id, is_active=True, is_in_stock=True,
                      resource_category="social_media")
    session.add_all([customer, product])
    session.flush()
    order = Order(order_no="ORD-TRANSIT-1", customer_id=customer.id, product_id=product.id,
                  region_id=region.id, region_name=region.name, quantity=1, billing_days=30,
                  unit_price=Decimal(95), total_amount=Decimal(95), amount=Decimal(95),
                  status="pending_delivery", payment_status="paid", order_type="purchase")
    session.add(order)
    session.flush()
    vps = TransitVps(
        name="US-01", group_code="us", base_url="https://panel.example.com/abc",
        username="admin", encrypted_password=None, connect_host="5.6.7.8",
        reality_private_key="priv-key", reality_public_key="pub-key", reality_short_id="d816",
        sni="yahoo.com", dest="yahoo.com:443", fingerprint="random",
        port_start=52000, port_end=52010, max_inbounds=10, is_enabled=True,
    )
    session.add(vps)
    session.add(TransitRoute(country_code="US", group_code="us", is_enabled=True))
    session.commit()
    yield session, order, vps
    session.close()
    engine.dispose()


def _patch_panel(monkeypatch, panel):
    monkeypatch.setattr(transit_service, "XuiClient", lambda *a, **k: panel)


def test_creates_inbound_outbound_and_route(ctx, monkeypatch):
    session, order, _vps = ctx
    panel = FakePanel()
    _patch_panel(monkeypatch, panel)

    endpoints = provision_transit(
        session, order=order, accounts=[FakeAccount()], country_code="US",
    )

    assert len(panel.added) == 1
    inbound = panel.added[0]
    assert inbound["protocol"] == "vless"
    assert inbound["port"] == 52000
    # 备注就是客户看到的节点名：地区-出口IP（手工口径）
    assert inbound["remark"] == "美国-1.2.3.4"

    socks = [item for item in panel.template["outbounds"] if item["protocol"] == "socks"]
    assert len(socks) == 1
    server = socks[0]["settings"]["servers"][0]
    assert server["address"] == "1.2.3.4"
    assert server["port"] == 1080
    assert server["users"] == [{"user": "u1", "pass": "p1"}]

    rule = panel.template["routing"]["rules"][0]
    assert rule["inboundTag"] == ["inbound-52000"]
    assert rule["outboundTag"] == socks[0]["tag"]
    assert panel.saved == 1 and panel.restarts == 1

    assert endpoints[0].exit_ip == "1.2.3.4"
    assert endpoints[0].link.startswith("vless://")
    assert "pbk=pub-key" in endpoints[0].link and "sid=d816" in endpoints[0].link
    assert "security=reality" in endpoints[0].link

    record = session.scalar(select(TransitProvision))
    assert record.status == "configured"
    assert record.inbound_port == 52000
    assert record.exit_ip == "1.2.3.4"


def test_rerun_does_not_create_second_inbound(ctx, monkeypatch):
    session, order, _vps = ctx
    panel = FakePanel()
    _patch_panel(monkeypatch, panel)

    first = provision_transit(session, order=order, accounts=[FakeAccount()], country_code="US")
    second = provision_transit(session, order=order, accounts=[FakeAccount()], country_code="US")

    assert len(panel.added) == 1
    assert panel.saved == 1 and panel.restarts == 1
    assert first[0].link == second[0].link
    assert session.scalar(select(TransitProvision.id)) is not None


def test_panel_save_failure_leaves_inbound_created(ctx, monkeypatch):
    session, order, _vps = ctx
    panel = FakePanel(fail_save=True)
    _patch_panel(monkeypatch, panel)

    with pytest.raises(TransitProvisionError) as excinfo:
        provision_transit(session, order=order, accounts=[FakeAccount()], country_code="US")

    assert excinfo.value.code == "XUI_SAVE_TEMPLATE"
    record = session.scalar(select(TransitProvision))
    # 入站已经建在面板上，不能标 failed——重试时要复用这个入站，只补出站与路由
    assert record.status == "inbound_created"
    assert record.inbound_id == 1
    assert len(panel.added) == 1


def test_no_machine_raises_not_configured(ctx, monkeypatch):
    session, order, vps = ctx
    vps.is_enabled = False
    session.commit()
    panel = FakePanel()
    _patch_panel(monkeypatch, panel)

    with pytest.raises(TransitNotConfigured):
        provision_transit(session, order=order, accounts=[FakeAccount()], country_code="US")
    assert panel.added == []


def test_unknown_country_raises_not_configured(ctx, monkeypatch):
    session, order, _vps = ctx
    panel = FakePanel()
    _patch_panel(monkeypatch, panel)

    with pytest.raises(TransitNotConfigured):
        provision_transit(session, order=order, accounts=[FakeAccount()], country_code="ZZ")
    assert panel.added == []


class FakeAdapter:
    def __init__(self):
        self.calls = 0

    def buy_static_ip(self, **_kwargs):
        self.calls += 1
        return PurchaseResult(order_number="UPSTREAM-9", accounts=(FakeAccount(),))


def test_auto_procure_delivers_vless_through_transit(ctx, monkeypatch):
    """整条链路：采购 → 中转 → 客户线路拿到的必须是 vless 入口，出口 IP 写住宅地址。"""
    session, order, _vps = ctx
    panel = FakePanel()
    _patch_panel(monkeypatch, panel)
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)

    settings = procurement_service.get_settings()
    monkeypatch.setattr(settings, "supplier_auto_procurement_enabled", True, raising=False)

    record = procurement_service.auto_procure_order(session, order)

    assert record.status == "succeeded"
    session.refresh(order)
    assert order.status == "delivered"
    line = session.scalar(select(CustomerLine).where(CustomerLine.order_id == order.id))
    assert line is not None
    assert line.node_link.startswith("vless://")
    assert line.static_ip == "1.2.3.4"
    prov = session.scalar(select(TransitProvision))
    assert prov.line_id == line.id


def test_generates_per_inbound_keys_when_machine_has_none(ctx, monkeypatch):
    """机器不填密钥时，每条入站独立生成 x25519 密钥对（与面板现状一致）。"""
    import json

    from app.services.reality_keys import public_key_from_private

    session, order, vps = ctx
    vps.reality_private_key = None
    vps.reality_public_key = None
    vps.reality_short_id = None
    session.commit()
    panel = FakePanel()
    _patch_panel(monkeypatch, panel)

    endpoints = provision_transit(session, order=order, accounts=[FakeAccount()], country_code="US")

    stream = json.loads(panel.added[0]["streamSettings"])
    reality = stream["realitySettings"]
    assert len(reality["privateKey"]) == 43
    assert reality["shortIds"][0]
    # 链接里的 pbk 必须与这条入站自己的私钥匹配，不能是机器级共用值
    assert public_key_from_private(reality["privateKey"]) in endpoints[0].link


def test_broken_panel_credential_stops_at_pending_transit(ctx, monkeypatch):
    """凭据解密失败（密钥轮换后的真实场景）：订单停在待配中转，上游单号必须留下。

    之前这里会抛 TransitCredentialError 冒到后台任务，导致事务回滚、单号丢失，
    采购记录永远停在 pending —— 钱花了、单号没了、客户什么都收不到。
    """
    from app.models import Notification

    session, order, vps = ctx
    vps.encrypted_password = "v1:broken"   # 密钥换过 / 密文损坏
    session.commit()

    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)
    monkeypatch.setattr(procurement_service.get_settings(), "supplier_auto_procurement_enabled",
                        True, raising=False)

    record = procurement_service.auto_procure_order(session, order)

    assert record.status == "awaiting_transit"
    assert record.error_code == "TRANSIT_UNEXPECTED"
    # 关键：上游单号必须已经落盘，否则出事只能去上游后台按订单号捞
    assert record.external_order_no == "UPSTREAM-9"

    session.refresh(order)
    assert order.status == "pending_transit"
    assert session.query(Notification).count() >= 1
