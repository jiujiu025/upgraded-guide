"""付款后自动采购静态住宅：状态机、幂等与失败不重试。

采购是扣钱动作，测试重点不是"能不能买"，而是：
1. 开关关着时什么都不做（默认行为，防止误开就扣款）；
2. 同一条线路重复触发只下单一次；
3. 上游报错时落库为 failed、订单留在待交付，不自动重试。
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.models import Customer, CustomerLine, Order, Procurement, Product, Region, User
from app.models.base import Base
from app.services import procurement_service
from app.suppliers.base import ProxyAccount, PurchaseResult, SupplierBusinessError


class FakeAdapter:
    """只实现采购需要的那一个方法，其余能力测试不关心。"""

    def __init__(self, accounts=1, error=None):
        self.accounts_count = accounts
        self.error = error
        self.calls = 0

    def buy_static_ip(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        accounts = tuple(
            ProxyAccount(
                external_id=str(100 + index), username=f"u{index}", password=f"p{index}",
                ip=f"10.0.0.{index}", port=1080, protocol="SOCKS5", expires_at=expires,
            )
            for index in range(1, self.accounts_count + 1)
        )
        return PurchaseResult(order_number="UPSTREAM-0001", accounts=accounts)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    region = Region(name="中国台湾", country_code="TW")
    session.add(region)
    session.flush()
    user = User(username="buyer", password_hash="x", role="customer", display_name="买家")
    session.add(user)
    session.flush()
    customer = Customer(name="买家", email="buyer@example.com", user_id=user.id)
    product = Product(name="中国台湾 静态住宅IP 30天", product_type="静态住宅IP", price=109,
                      billing_days=30, region_id=region.id, is_active=True, is_in_stock=True,
                      resource_category="static_residential")
    session.add_all([customer, product])
    session.flush()
    order = Order(order_no="ORD-AUTO-1", customer_id=customer.id, product_id=product.id,
                  region_id=region.id, region_name=region.name, quantity=1, billing_days=30,
                  unit_price=Decimal("109"), total_amount=Decimal("109"), amount=Decimal("109"),
                  status="paid", payment_status="paid", order_type="purchase")
    session.add(order)
    session.commit()
    yield session, order
    session.close()
    engine.dispose()


def enable_procurement(monkeypatch, enabled: bool = True):
    settings = get_settings()
    monkeypatch.setattr(settings, "supplier_auto_procurement_enabled", enabled, raising=False)


def test_disabled_switch_does_nothing(db, monkeypatch):
    session, order = db
    enable_procurement(monkeypatch, False)
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)

    assert procurement_service.auto_procure_order(session, order) is None
    assert adapter.calls == 0
    assert session.scalar(select(Procurement.id)) is None


def test_procure_creates_line_and_delivers_order(db, monkeypatch):
    session, order = db
    enable_procurement(monkeypatch)
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)

    record = procurement_service.auto_procure_order(session, order)

    assert record.status == "succeeded"
    assert record.external_order_no == "UPSTREAM-0001"
    assert adapter.calls == 1
    # 上游参数按订单映射：30 天 → timelen=1，数量 1，国家 TW
    assert adapter.last_kwargs["timelen"] == 1
    assert adapter.last_kwargs["quantity"] == 1
    assert adapter.last_kwargs["country_code"] == "TW"
    assert adapter.last_kwargs["remark"] == "ORD-AUTO-1"

    line = session.scalar(select(CustomerLine).where(CustomerLine.order_id == order.id))
    assert line is not None
    assert line.static_ip == "10.0.0.1"
    assert line.node_link.startswith("socks5://u1:p1@10.0.0.1:1080")
    assert "/api/sub/" in line.subscription_url

    session.refresh(order)
    assert order.status == "delivered"
    assert order.delivered_quantity == 1


def test_second_call_does_not_buy_again(db, monkeypatch):
    session, order = db
    enable_procurement(monkeypatch)
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)

    first = procurement_service.auto_procure_order(session, order)
    second = procurement_service.auto_procure_order(session, order)

    assert adapter.calls == 1
    assert first.id == second.id


def test_supplier_error_keeps_order_pending_and_marks_failed(db, monkeypatch):
    session, order = db
    enable_procurement(monkeypatch)
    adapter = FakeAdapter(error=SupplierBusinessError("余额不足"))
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)

    record = procurement_service.auto_procure_order(session, order)

    assert record.status == "failed"
    assert "余额不足" in record.error_message
    session.refresh(order)
    assert order.status == "pending_delivery"   # 转人工，不自动重试
    assert session.scalar(select(CustomerLine).where(CustomerLine.order_id == order.id)) is None


def test_non_residential_product_is_skipped(db, monkeypatch):
    session, order = db
    enable_procurement(monkeypatch)
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)
    product = session.get(Product, order.product_id)
    product.resource_category = "social_media"
    session.commit()

    assert procurement_service.auto_procure_order(session, order) is None
    assert adapter.calls == 0


def test_unsupported_period_falls_back_to_manual(db, monkeypatch):
    session, order = db
    enable_procurement(monkeypatch)
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)
    order.billing_days = 45   # 上游只有 7/30/90/180/360
    session.commit()

    record = procurement_service.auto_procure_order(session, order)

    assert record.status == "manual"
    assert record.error_code == "UNSUPPORTED_PERIOD"
    assert adapter.calls == 0


def test_manual_retry_endpoint_guards(db, monkeypatch):
    """人工重试：没收款不许开；已经买过的不重复买。"""
    from fastapi import BackgroundTasks, HTTPException

    from app.api.routes import retry_order_procurement

    session, order = db
    admin = User(username="root", password_hash="x", role="admin", display_name="管理员")
    session.add(admin)
    session.commit()
    enable_procurement(monkeypatch)

    # 未收款：拒绝
    order.payment_status = "unpaid"
    session.commit()
    with pytest.raises(HTTPException) as exc:
        retry_order_procurement(order.id, BackgroundTasks(), db=session, user=admin)
    assert exc.value.status_code == 409

    # 已收款、没有成功记录：入队
    order.payment_status = "paid"
    session.commit()
    result = retry_order_procurement(order.id, BackgroundTasks(), db=session, user=admin)
    assert result["status"] == "queued"

    # 已有成功记录：不重复下单
    adapter = FakeAdapter()
    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: adapter)
    procurement_service.auto_procure_order(session, order)
    again = retry_order_procurement(order.id, BackgroundTasks(), db=session, user=admin)
    assert again["status"] == "already_delivered"
