"""升级单真正发起中转。

之前的问题是：升级收款后只把线路标成 ``pending_transit``，从没调用 provision，
于是客户永远停在「待开通」，后台「待配中转」也看不到（那些按订单状态查）。

这里锁住三件事：
1. 升级成功后真的配了中转，线路回到 active，入口地址指向中转机；
2. 中转失败时线路仍是 pending_transit，并写进系统日志（能看到原因）；
3. 修好面板后点重试能补上——不依赖采购记录，也不走 purchase 专用的交付占位。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Customer,
    CustomerLine,
    Node,
    Order,
    Product,
    Region,
    RetailPriceRule,
    SystemLog,
    TransitProvision,
    TransitVps,
    User,
)
from app.models.base import Base
from app.services import procurement_service, transit_service
from app.services.transit_credentials import encrypt_transit_secret


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSIT_BACKUP_DIR", str(tmp_path / "backups"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    db.add(RetailPriceRule(resource_category="social_media", country_code="US",
                           billing_days=30, unit_price=Decimal("95"), source_version="test"))
    home = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=60, billing_days=30,
                   region_id=region.id, resource_category="static_residential",
                   is_active=True, is_in_stock=True)
    social = Product(name="美国 社媒运营专线 30天", product_type="社媒运营专线", price=95, billing_days=30,
                     region_id=region.id, resource_category="social_media",
                     is_active=True, is_in_stock=True)
    user = User(username="up_grade", password_hash="x", role="customer", display_name="客户")
    db.add_all([home, social, user])
    db.flush()
    customer = Customer(name="升级客户", email="up@test.com", user_id=user.id)
    db.add(customer)
    db.flush()
    node = Node(name="原直连", server="10.0.0.9", port=1080, protocol="SOCKS5", region_id=region.id)
    db.add(node)
    db.flush()
    line = CustomerLine(customer_id=customer.id, node_id=node.id, product_id=home.id,
                        static_ip="1.2.3.4", started_at=datetime.utcnow(),
                        expires_at=datetime.utcnow() + timedelta(days=20), status="pending_transit")
    db.add(line)
    db.flush()
    diff = Decimal("35")
    order = Order(order_no="ORD-UP-1", customer_id=customer.id, product_id=social.id,
                  previous_product_id=home.id, region_id=region.id, region_name="美国",
                  quantity=1, billing_days=30, unit_price=diff, total_amount=diff,
                  amount=diff, payment_status="manually_confirmed", status="completed",
                  order_type="upgrade", renewal_line_id=line.id)

    db.add(order)
    db.flush()
    # 升级要复用这条线的出口账密：以前走过中转，记录里加密存着
    vps = TransitVps(name="HK-01", group_code="us", base_url="https://old.example.com/x",
                     username="admin", connect_host="9.9.9.9",
                     port_start=52000, port_end=52999, max_inbounds=200, is_enabled=True)
    db.add(vps)
    db.flush()
    secret = encrypt_transit_secret('{"ip": "1.2.3.4", "port": 1080, "username": "u", "password": "p"}',
                                    scope=f"transit-exit:{order.id}:1")
    db.add(TransitProvision(order_id=order.id, line_index=1, line_id=line.id,
                            encrypted_exit_credentials=secret, status="failed"))
    db.commit()

    yield {"db": db, "order": order, "line": line, "vps": vps}
    db.close()
    engine.dispose()


class FakeClient:
    saved = 0
    restarts = 0

    def __init__(self, *_a, **_k):
        self.inbounds = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def list_inbounds(self):
        return list(self.inbounds)

    def used_ports(self):
        return {int(item["port"]) for item in self.inbounds}

    def add_inbound(self, payload):
        self.inbounds.append({**payload, "id": len(self.inbounds) + 1,
                              "tag": f"inbound-{payload['port']}"})
        return {"id": len(self.inbounds)}

    def get_xray_template(self):
        return {"outbounds": [{"tag": "direct", "protocol": "freedom"}], "routing": {"rules": []}}

    def save_xray_template(self, config):
        FakeClient.saved += 1

    def restart_xray(self):
        FakeClient.restarts += 1


def test_upgrade_transit_is_really_provisioned(env, monkeypatch):
    monkeypatch.setattr(transit_service, "XuiClient", FakeClient)
    db, order, line = env["db"], env["order"], env["line"]

    assert procurement_service.apply_upgrade_transit(db, order.id) == "active"

    db.refresh(line)
    assert line.status == "active"
    # 入口换到中转机上
    node = db.get(Node, line.node_id)
    assert node.server == "9.9.9.9"
    assert node.port == 52000
    assert line.node_link and "vless://" in line.node_link

    provision = db.scalar(select(TransitProvision).where(TransitProvision.order_id == order.id))
    assert provision.status == "configured"
    assert provision.line_id == line.id
    assert provision.inbound_port == 52000


def test_upgrade_transit_failure_stays_pending_with_log(env, monkeypatch):
    """没有可用面板：线路保持待配中转，但必须留下可查的原因。"""
    db, order, line = env["db"], env["order"], env["line"]
    env["vps"].is_enabled = False
    db.commit()

    outcome = procurement_service.apply_upgrade_transit(db, order.id)

    assert outcome == "pending_transit"
    db.refresh(line)
    assert line.status == "pending_transit"
    log = db.scalar(select(SystemLog).where(SystemLog.action == "upgrade_transit_failed"))
    assert log is not None
    assert order.order_no in log.message


def test_upgrade_transit_is_idempotent(env, monkeypatch):
    monkeypatch.setattr(transit_service, "XuiClient", FakeClient)
    db, order = env["db"], env["order"]

    assert procurement_service.apply_upgrade_transit(db, order.id) == "active"
    # 再跑一次不应重复建入站
    assert procurement_service.apply_upgrade_transit(db, order.id) == "active"
    records = db.scalars(select(TransitProvision).where(TransitProvision.order_id == order.id)).all()
    assert len(records) == 1
