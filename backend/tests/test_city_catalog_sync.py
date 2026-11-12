"""资源池 1 城市 + IP 段同步：客户能直接选到真实号段。

规则：
- 能匹配到已有城市码（别名表）→ 合并号段，不新建城市；
- 匹配不上的上游城市 → 落库为停用（待归属），不进客户下拉；
- 幂等：重复执行不会产生重复城市或重复号段。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.supplier_routes import get_global_proxy_adapter
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import CatalogCityOption, Product, Region, User
from app.models.base import Base
from app.services.city_catalog_sync import sync_city_segments, sync_city_segments_985
from app.services.city_option_service import CitySelectionError, customer_options, resolve_choice
from app.services.session_service import create_session
from app.suppliers.base import SupplierRegionItem


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def list_countries(self, product_type_id):
        self.calls.append(("countries", product_type_id))
        return [SupplierRegionItem("653", "US", "美国")]

    def list_cities(self, product_type_id, country_code=None, country_id=None):
        self.calls.append(("cities", country_code))
        return [
            SupplierRegionItem("653", "US", "美国", "3911", "Los Angeles"),
            SupplierRegionItem("653", "US", "美国", "3915", "Washington"),
        ]

    def list_ip_segments(self, *, country_code=None, city_code=None, page=1, page_size=20, **kwargs):
        self.calls.append(("segments", country_code, city_code))
        if city_code == "Los Angeles":
            return (["149.52", "38.30"] if page == 1 else []), 2
        return ["44.1"], 1

    def close(self):
        pass


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, Session()


def seed_lax(db):
    db.add(Region(name="美国", country_code="US"))
    db.add(CatalogCityOption(resource_category="static_residential", country_code="US",
                             city_code="LAX", city_name="洛杉矶", ip_segments="23.94.5",
                             is_enabled=True, sort_order=10, data_source="manual"))
    db.commit()


def test_sync_merges_segments_and_keeps_unmatched_cities_pending():
    engine, db = make_session()
    try:
        seed_lax(db)
        result = sync_city_segments(db, FakeAdapter())
        db.commit()

        assert result.countries == 1
        assert result.cities_seen == 2
        assert result.matched == 1
        assert result.created_pending == 1
        assert result.errors == []

        lax = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
        segments = lax.ip_segments.split(",")
        assert segments == ["23.94.5", "149.52", "38.30"]
        assert lax.is_enabled is True

        pending = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_name == "Washington"))
        assert pending is not None
        assert pending.is_enabled is False
        assert pending.note and "待归属" in pending.note
        assert pending.ip_segments == "44.1"
    finally:
        db.close()
        engine.dispose()


def test_sync_is_idempotent():
    engine, db = make_session()
    try:
        seed_lax(db)
        sync_city_segments(db, FakeAdapter())
        db.commit()
        sync_city_segments(db, FakeAdapter())
        db.commit()

        assert db.scalar(select(func.count()).select_from(CatalogCityOption)) == 2
        lax = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
        assert lax.ip_segments == "23.94.5,149.52,38.30"
    finally:
        db.close()
        engine.dispose()


class Fake985Adapter:
    """985 的库存接口：同城 LAX，但段是另一个池的段。"""

    def list_inventory(self, static_proxy_type="premium"):
        return [
            {"country": "US", "city": "Los Angeles", "can_spec": True},
            {"country": "US", "city": "Nowhere", "can_spec": False},
        ]

    def list_ip_segments(self, *, country=None, city=None, static_proxy_type="premium"):
        return ["9.9.9"]


def test_985_sync_never_opens_cities_before_purchase_chain_exists():
    """985 只有只读接口，采购走池 1；同步进来的城市不能对客户开放。

    否则客户会在下拉里选到一个"下单必然失败"的城市/号段——那是退款的前置条件。
    """
    engine, db = make_session()
    try:
        result = sync_city_segments_985(db, Fake985Adapter())
        db.commit()

        lax = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
        assert lax is not None
        assert lax.is_enabled is False
        assert lax.data_source == "supplier_985"
        assert "暂不开放" in (lax.note or "")
        assert lax.ip_segments == "9.9.9"  # 段可以预研落库，但不展示给客户
        assert result.created_pending == 2
    finally:
        db.close()
        engine.dispose()


def test_985_sync_does_not_pollute_pool_one_rows():
    """同一城市已属于池 1 时，985 的段不能并进来——客户会选到池 1 买不到的段。"""
    engine, db = make_session()
    try:
        seed_lax(db)
        sync_city_segments(db, FakeAdapter())   # 池 1 先同步，LAX 行归属池 1
        db.commit()
        before = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
        before_segments, before_source = before.ip_segments, before.data_source

        sync_city_segments_985(db, Fake985Adapter())
        db.commit()

        after = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
        assert after.ip_segments == before_segments
        assert "9.9.9" not in after.ip_segments
        assert after.data_source == before_source
        assert after.is_enabled is True
    finally:
        db.close()
        engine.dispose()


def test_985_sync_disables_rows_opened_by_older_version():
    """早期版本会直接开放匹配上别名的 985 城市；重跑同步要把它们纠正为关闭。"""
    engine, db = make_session()
    try:
        db.add(CatalogCityOption(resource_category="static_residential", country_code="US",
                                 city_code="LAX", city_name="Los Angeles", ip_segments="",
                                 is_enabled=True, sort_order=500, data_source="supplier_985"))
        db.commit()

        sync_city_segments_985(db, Fake985Adapter())
        db.commit()

        lax = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "LAX"))
        assert lax.is_enabled is False
        assert "暂不开放" in (lax.note or "")
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def api():
    engine, db = make_session()
    seed_lax(db)
    admin = User(username="seg_admin", password_hash=hash_password("Admin_123456"),
                 role="super_admin", display_name="管理员")
    customer = User(username="seg_customer", password_hash=hash_password("Customer_123456"),
                    role="customer", display_name="客户")
    db.add_all([admin, customer])
    db.commit()
    headers = {}
    for key, user in (("admin", admin), ("customer", customer)):
        session, _ = create_session(db, user)
        headers[key] = {"Authorization": f"Bearer {create_access_token(user.id, user.role, session.id, user.auth_version)}"}
    db.commit()

    def override_db():
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_global_proxy_adapter] = lambda: FakeAdapter()
    state = {"client": TestClient(app), "headers": headers}
    yield state
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_sync_endpoint_requires_admin(api):
    url = "/api/admin/suppliers/sync/city-segments?product_type_id=3&country_code=US"
    assert api["client"].post(url).status_code == 401
    assert api["client"].post(url, headers=api["headers"]["customer"]).status_code == 403


def test_sync_endpoint_updates_customer_visible_segments(api):
    response = api["client"].post(
        "/api/admin/suppliers/sync/city-segments?product_type_id=3&country_code=US",
        headers=api["headers"]["admin"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    # LA 合并后 3 段（原有 1 + 上游 2），Washington 1 段，合计 4
    assert body["segments_saved"] == 4

    listed = api["client"].get("/api/admin/catalog-management/cities?country_code=US",
                               headers=api["headers"]["admin"]).json()
    lax = next(item for item in listed["items"] if item["city_code"] == "LAX")
    assert lax["segments"] == ["23.94.5", "149.52", "38.30"]
    washington = next(item for item in listed["items"] if item["city_name"] == "Washington")
    assert washington["is_enabled"] is False


def _customer_product(db):
    region = db.scalar(select(Region).where(Region.country_code == "US"))
    product = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=60,
                      billing_days=30, region_id=region.id, resource_category="static_residential")
    db.add(product)
    db.commit()
    return product


def test_customer_options_only_trust_pool_one_when_available():
    engine, db = make_session()
    try:
        seed_lax(db)  # manual / LAX / enabled
        db.add(CatalogCityOption(resource_category="static_residential", country_code="US",
                                 city_code="NYC", city_name="纽约", ip_segments="44.1",
                                 is_enabled=True, data_source="supplier_global_proxy"))
        db.add(CatalogCityOption(resource_category="static_residential", country_code="US",
                                 city_code="SFO", city_name="旧金山", ip_segments="45.1",
                                 is_enabled=True, data_source="supplier_985"))
        db.commit()
        product = _customer_product(db)

        listed = customer_options(db, product)
        assert [item["city_code"] for item in listed["items"]] == ["NYC"]
        with pytest.raises(CitySelectionError):
            resolve_choice(db, product, db.get(Region, product.region_id), "LAX", None)
        choice = resolve_choice(db, product, db.get(Region, product.region_id), "NYC", None)
        assert choice.city_code == "NYC"
    finally:
        db.close()
        engine.dispose()


def test_sync_disables_pool_one_cities_missing_from_upstream():
    engine, db = make_session()
    try:
        db.add(Region(name="美国", country_code="US"))
        for code, name in (("SFO", "旧金山"), ("LAX", "洛杉矶")):
            db.add(CatalogCityOption(resource_category="static_residential", country_code="US",
                                     city_code=code, city_name=name, ip_segments="",
                                     is_enabled=True, data_source="supplier_global_proxy"))
        db.add(CatalogCityOption(resource_category="static_residential", country_code="US",
                                 city_code="BOS", city_name="波士顿", ip_segments="",
                                 is_enabled=True, data_source="manual"))
        db.commit()

        result = sync_city_segments(db, FakeAdapter())
        db.commit()

        assert result.disabled_stale == 1
        sfo = db.scalar(select(CatalogCityOption).where(CatalogCityOption.city_code == "SFO"))
        assert sfo.is_enabled is False
        assert "未返回" in sfo.note
        manual = db.scalar(select(CatalogCityOption).where(
            CatalogCityOption.city_code == "BOS",
            CatalogCityOption.data_source == "manual",
        ))
        # 只处理池 1 自己的行，手工行不应被同步顺手改掉
        assert manual.is_enabled is True
    finally:
        db.close()
        engine.dispose()
