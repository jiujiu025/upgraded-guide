"""后台运行时开关与 985 库存解析。

开关的重点是「不用重启就生效」和「环境变量只是默认值」，所以这里直接测服务层；
985 的库存有两三种返回结构，解析函数要都能吃下去。
"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.models import Customer, Order, Product, Region, User
from app.models.base import Base
from app.services import procurement_service
from app.services.settings_service import AUTO_PROCUREMENT_KEY, get_flag, set_flag
from app.suppliers.nineeightfive import _flatten_inventory
from app.suppliers.base import PurchaseResult
from decimal import Decimal


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def test_flag_defaults_to_env_then_overrides(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "supplier_auto_procurement_enabled", True, raising=False)

    value, source = get_flag(db, AUTO_PROCUREMENT_KEY)
    assert value is True and source == "env"

    set_flag(db, AUTO_PROCUREMENT_KEY, False)
    db.commit()

    value, source = get_flag(db, AUTO_PROCUREMENT_KEY)
    assert value is False and source == "runtime"


def test_procurement_reads_runtime_flag(db, monkeypatch):
    """后台关掉后，即使环境变量是开的也不采购——这是能"随时掐断"的关键。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "supplier_auto_procurement_enabled", True, raising=False)
    set_flag(db, AUTO_PROCUREMENT_KEY, False)
    db.commit()

    region = Region(name="中国台湾", country_code="TW")
    db.add(region)
    db.flush()
    user = User(username="u1", password_hash="x", role="customer", display_name="客户")
    db.add(user)
    db.flush()
    customer = Customer(name="客户", email="c@example.com", user_id=user.id)
    product = Product(name="台湾住宅", product_type="静态住宅IP", price=109, billing_days=30,
                      region_id=region.id, is_active=True, is_in_stock=True,
                      resource_category="static_residential")
    db.add_all([customer, product])
    db.flush()
    order = Order(order_no="ORD-RT-1", customer_id=customer.id, product_id=product.id,
                  region_id=region.id, quantity=1, billing_days=30, unit_price=Decimal("109"),
                  total_amount=Decimal("109"), amount=Decimal("109"), status="pending_delivery",
                  payment_status="paid", order_type="purchase")
    db.add(order)
    db.commit()

    calls = {"n": 0}

    class FakeAdapter:
        def buy_static_ip(self, **kwargs):
            calls["n"] += 1
            return PurchaseResult(order_number="X", accounts=())

    monkeypatch.setattr(procurement_service, "build_readonly_adapter", lambda *a, **k: FakeAdapter())

    assert procurement_service.auto_procure_order(db, order) is None
    assert calls["n"] == 0

    # 后台打开后，同一订单立刻可以采购
    set_flag(db, AUTO_PROCUREMENT_KEY, True)
    db.commit()
    record = procurement_service.auto_procure_order(db, order)
    assert record is not None and calls["n"] == 1


def test_flatten_inventory_handles_flat_list():
    rows = _flatten_inventory([
        {"country": "US", "city": "Los Angeles", "stock": 5, "price": 3.5, "can_spec": True},
        {"country": "jp", "city_name": "Tokyo", "inventory": "2"},
    ])
    assert rows[0]["country"] == "US" and rows[0]["stock"] == 5 and rows[0]["can_spec"] is True
    assert rows[1]["country"] == "JP" and rows[1]["stock"] == 2


def test_flatten_inventory_handles_grouped_dict():
    rows = _flatten_inventory({
        "US": {"cities": [{"city": "New York", "stock": 3, "can_spec": False}]},
        "HK": [{"city": "Hong Kong", "stock": 9, "can_spec": True}],
    })
    assert {(row["country"], row["city"]) for row in rows} == {("US", "New York"), ("HK", "Hong Kong")}


def test_flatten_inventory_ignores_garbage():
    assert _flatten_inventory(None) == []
    assert _flatten_inventory("oops") == []
    assert _flatten_inventory([{"city": "没有国家"}]) == []


def test_sync_985_writes_cities_and_segments(db):
    """985 同步：城市与号段落库，但一律不开放（采购链路未接入）。"""
    from app.services.city_catalog_sync import sync_city_segments_985
    from app.models import CatalogCityOption

    class FakeAdapter:
        def list_inventory(self, static_proxy_type="premium"):
            return [
                {"country": "US", "city": "Los Angeles", "stock": 4, "can_spec": True},
                {"country": "US", "city": "Nowhereville", "stock": 1, "can_spec": False},
            ]

        def list_ip_segments(self, *, country, city, static_proxy_type="premium"):
            return ["1.1.1", "1.1.2"]

    result = sync_city_segments_985(db, FakeAdapter())
    db.commit()

    assert result.cities_seen == 2
    assert result.segments_saved == 2
    lax = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
    # 985 目前只有只读接口，采购走池 1；开放给客户就会选到“下单必然失败”的段。
    assert lax is not None and lax.is_enabled is False and "1.1.1" in lax.ip_segments
    assert "未接入" in (lax.note or "")
    pending = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_name == "Nowhereville"))
    assert pending is not None and pending.is_enabled is False
