from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    CatalogPublication, Region, SupplierInventorySnapshot, SupplierProduct, SupplierQuote, SupplierRegion,
)
from app.models.base import Base
from app.services.supplier_service import COUNTRIES_ENDPOINT, ensure_pool_one
from app.suppliers.base import InventoryItem, SupplierProductItem, SupplierRegionItem, SupplierTimeoutError
from app.suppliers.base import QuoteItem
from app.tasks import registry, suppliers

NOW = datetime(2026, 11, 8, 12, 0, 0)


class FakeAdapter:
    """记录调用次数，用于验证"新鲜就不拉"与轮转行为。"""

    def __init__(self, *, fail_countries=(), fail_steps=()):
        self.calls: list[tuple[str, object]] = []
        self.fail_countries = set(fail_countries)
        self.fail_steps = set(fail_steps)

    def list_products(self):
        self.calls.append(("products", None))
        if "products" in self.fail_steps:
            raise SupplierTimeoutError("商品接口超时")
        return [SupplierProductItem("2", "静态住宅", None), SupplierProductItem("3", "原生住宅", None)]

    def list_countries(self, product_type_id):
        self.calls.append(("countries", product_type_id))
        if "countries" in self.fail_steps:
            raise SupplierTimeoutError("国家接口超时")
        return [
            SupplierRegionItem("1", "US", "美国"),
            SupplierRegionItem("2", "HK", "中国香港"),
            SupplierRegionItem("3", "JP", "日本"),
        ]

    def list_cities(self, product_type_id, country_code=None, country_id=None):
        self.calls.append(("cities", country_code))
        if "cities" in self.fail_steps:
            raise SupplierTimeoutError("城市接口超时")
        return [SupplierRegionItem("1", "US", "美国", "11", "洛杉矶")]

    def get_inventory(self, product_type_id, country_code):
        self.calls.append(("inventory", country_code))
        if country_code in self.fail_countries:
            raise SupplierTimeoutError(f"{country_code} 超时")
        return [InventoryItem(country_code=country_code, city_name=None, availability="available")]

    def get_quotes(self, **parameters):
        self.calls.append(("quotes", parameters))
        return [QuoteItem(external_product_ref="2", unit_cost="30", country_code="US", duration_days=30)]

    def close(self):
        self.calls.append(("close", None))


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = Session()
    ensure_pool_one(db)
    db.commit()
    yield db, Session
    db.close()
    engine.dispose()


def _seed_catalog(db):
    adapter = FakeAdapter()
    suppliers.refresh_catalog(db, now=NOW, adapter=adapter)
    db.commit()
    return adapter


def _last_checked(db):
    return dict(db.execute(
        select(SupplierInventorySnapshot.country_code, SupplierInventorySnapshot.checked_at)
        .order_by(SupplierInventorySnapshot.checked_at)
    ).all())


def test_refresh_catalog_syncs_once_then_skips_while_fresh(env):
    db, _ = env
    first = FakeAdapter()

    result = suppliers.refresh_catalog(db, now=NOW, adapter=first)
    db.commit()
    assert result == {"products": 2, "countries": 3, "cities": 1, "errors": 0}
    assert [call[0] for call in first.calls] == ["products", "countries", "cities"]

    second = FakeAdapter()
    skipped = suppliers.refresh_catalog(db, now=NOW + timedelta(minutes=5), adapter=second)
    assert second.calls == []
    assert "note" in skipped

    refreshed = suppliers.refresh_catalog(db, now=NOW + timedelta(minutes=3000), adapter=FakeAdapter())
    assert refreshed["products"] == 2


def test_refresh_catalog_force_bypasses_ttl(env):
    db, _ = env
    _seed_catalog(db)
    forced = suppliers.refresh_catalog(db, now=NOW + timedelta(minutes=1), force=True, adapter=FakeAdapter())
    assert forced["products"] == 2 and forced["errors"] == 0


def test_refresh_catalog_reports_partial_failure_without_blocking_others(env):
    db, _ = env
    adapter = FakeAdapter(fail_steps={"cities"})
    result = suppliers.refresh_catalog(db, now=NOW, adapter=adapter)
    db.commit()
    assert result["products"] == 2
    assert result["countries"] == 3
    assert result["cities"] == 0
    assert result["errors"] == 1
    assert result["failed_steps"] == "cities"
    # 城市失败不影响国家已经落库
    assert db.scalar(select(SupplierRegion).where(SupplierRegion.source_endpoint == COUNTRIES_ENDPOINT)) is not None


def test_refresh_catalog_raises_when_every_step_fails(env):
    db, _ = env
    adapter = FakeAdapter(fail_steps={"products", "countries", "cities"})
    with pytest.raises(RuntimeError):
        suppliers.refresh_catalog(db, now=NOW, adapter=adapter)


def test_refresh_inventory_rotates_batches_and_covers_all_countries(env):
    db, _ = env
    _seed_catalog(db)

    first = FakeAdapter()
    result = suppliers.refresh_inventory(db, now=NOW, batch_size=2, stale_minutes=30, adapter=first)
    db.commit()
    assert result["countries"] == 2
    assert result["pending"] == 1
    assert [call[1] for call in first.calls] == ["US", "HK"]

    # 已刷过的两个国家仍在有效期内，所以下一轮只会补剩下的日本
    second = FakeAdapter()
    again = suppliers.refresh_inventory(db, now=NOW + timedelta(minutes=5), batch_size=2, stale_minutes=30, adapter=second)
    db.commit()
    assert [call[1] for call in second.calls] == ["JP"]
    assert again["pending"] == 0

    # 全部新鲜后不再发请求
    third = FakeAdapter()
    idle = suppliers.refresh_inventory(db, now=NOW + timedelta(minutes=6), batch_size=2, stale_minutes=30, adapter=third)
    assert third.calls == []
    assert "note" in idle


def test_refresh_inventory_retries_stale_countries_first(env):
    db, _ = env
    _seed_catalog(db)
    suppliers.refresh_inventory(db, now=NOW, batch_size=3, stale_minutes=30, adapter=FakeAdapter())
    db.commit()
    later = FakeAdapter()
    suppliers.refresh_inventory(db, now=NOW + timedelta(minutes=31), batch_size=1, stale_minutes=30, adapter=later)
    db.commit()
    # 三个国家同时过期时，按上次查询时间升序取最早的那个
    assert [call[1] for call in later.calls] == ["US"]


def test_refresh_inventory_isolates_single_country_failure(env):
    db, _ = env
    _seed_catalog(db)
    adapter = FakeAdapter(fail_countries={"HK"})
    result = suppliers.refresh_inventory(db, now=NOW, batch_size=3, stale_minutes=30, adapter=adapter)
    db.commit()
    assert result["countries"] == 2
    assert result["failures"] == 1
    assert result["failed_countries"] == "HK"


def test_refresh_inventory_raises_when_whole_batch_fails(env):
    db, _ = env
    _seed_catalog(db)
    adapter = FakeAdapter(fail_countries={"US", "HK", "JP"})
    with pytest.raises(RuntimeError):
        suppliers.refresh_inventory(db, now=NOW, batch_size=3, stale_minutes=30, adapter=adapter)


def test_refresh_inventory_skips_before_catalog_is_synced(env):
    db, _ = env
    result = suppliers.refresh_inventory(db, now=NOW, batch_size=3, stale_minutes=30, adapter=FakeAdapter())
    assert "skipped" in result


def test_refresh_inventory_prunes_old_snapshots_but_keeps_published_ones(env):
    db, _ = env
    _seed_catalog(db)
    supplier_product = db.scalar(select(SupplierProduct).where(SupplierProduct.external_product_ref == "2"))
    region = db.scalar(select(SupplierRegion).where(SupplierRegion.country_code == "US"))

    old = NOW - timedelta(days=30)
    keep = SupplierInventorySnapshot(
        supplier_id=supplier_product.supplier_id, supplier_product_id=supplier_product.id,
        supplier_region_id=region.id, country_code="US", city_name=None, availability="available",
        source_endpoint="/externalapi/inventory/getBuyProductSku", checked_at=old,
    )
    drop = SupplierInventorySnapshot(
        supplier_id=supplier_product.supplier_id, supplier_product_id=supplier_product.id,
        supplier_region_id=region.id, country_code="US", city_name="洛杉矶", availability="available",
        source_endpoint="/externalapi/inventory/getBuyProductSku", checked_at=old,
    )
    db.add_all([keep, drop])
    db.flush()
    local_region = db.scalar(select(Region))
    db.add(CatalogPublication(
        product_id=1, region_id=local_region.id, version=1,
        supplier_product_id=supplier_product.id, supplier_region_id=region.id,
        inventory_snapshot_id=keep.id, quote_snapshot_id=0, actor_id=1,
        before_values={}, after_values={}, snapshot_version={},
    ))
    db.commit()

    result = suppliers.refresh_inventory(
        db, now=NOW, batch_size=0, stale_minutes=30, retention_days=7, adapter=FakeAdapter(),
    )
    db.commit()
    assert result["pruned"] == 1
    remaining = {row.city_name for row in db.scalars(select(SupplierInventorySnapshot)).all()}
    assert remaining == {None}


def test_refresh_quotes_skips_while_fresh_then_syncs(env):
    db, _ = env
    _seed_catalog(db)

    first = FakeAdapter()
    assert suppliers.refresh_quotes(db, now=NOW, adapter=first) == {"quotes": 1}
    db.commit()

    idle = FakeAdapter()
    assert "note" in suppliers.refresh_quotes(db, now=NOW + timedelta(minutes=10), adapter=idle)
    assert idle.calls == []

    later = FakeAdapter()
    assert suppliers.refresh_quotes(db, now=NOW + timedelta(minutes=3000), adapter=later)["quotes"] == 1
    db.commit()
    assert db.scalar(select(SupplierQuote)) is not None


def test_supplier_tasks_are_registered_in_catalog_first_order():
    names = [spec.name for spec in registry.SPECS]
    assert names.index("supplier_catalog_refresh") < names.index("supplier_inventory_refresh")
    # 库存是唯一逐国串行请求的任务，放在最后，避免拖慢关单与提醒
    assert names[-1] == "supplier_inventory_refresh"


def test_registry_handlers_wire_settings_into_tasks(env, monkeypatch):
    db, _ = env
    adapter = FakeAdapter()
    monkeypatch.setattr(suppliers, "has_configured_api_key", lambda _db: True)
    monkeypatch.setattr(suppliers, "build_readonly_adapter", lambda _db: adapter)

    catalog = registry.TASKS["supplier_catalog_refresh"].handler(db, NOW)
    db.commit()
    assert catalog["products"] == 2

    inventory = registry.TASKS["supplier_inventory_refresh"].handler(db, NOW)
    db.commit()
    # batch_size 来自配置（默认 10），三个国家一次就能全刷完
    assert inventory["countries"] == 3
    assert inventory["pending"] == 0

    quotes = registry.TASKS["supplier_quotes_refresh"].handler(db, NOW)
    db.commit()
    assert quotes["quotes"] == 1
