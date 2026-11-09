"""官网产品目录接口（docs/官网接真数据设计.md）。

约束：
- 按分类聚合，不把供应商同步出来的每条 SKU 平铺到官网；
- 列表只给起价与覆盖地区，详情才给逐地区逐周期的价格表；
- 价格全部来自数据库，前端不算价；
- 未知分类 404，停用/停售商品不出现。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models import Product, Region
from app.models.base import Base
from app.services.registration_service import rate_limiter


@pytest.fixture()
def api():
    rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    us = Region(name="美国", country_code="US")
    jp = Region(name="日本", country_code="JP")
    db.add_all([us, jp])
    db.flush()
    db.add_all([
        # 同一分类 + 同一国家 + 不同周期 = 三条 SKU，官网应合成一个分类/一个地区
        Product(name="美国 独享静态住宅 30天", product_type="独享静态住宅（普通）", price=60,
                billing_days=30, region_id=us.id, resource_category="static_residential"),
        Product(name="美国 独享静态住宅 90天", product_type="独享静态住宅（普通）", price=180,
                billing_days=90, region_id=us.id, resource_category="static_residential"),
        Product(name="日本 独享静态住宅 30天", product_type="独享静态住宅（普通）", price=80,
                billing_days=30, region_id=jp.id, resource_category="static_residential"),
        Product(name="日本 独享静态住宅 90天", product_type="独享静态住宅（普通）", price=240,
                billing_days=90, region_id=jp.id, resource_category="static_residential", is_in_stock=False),
        # 固定价商品（resource_category 为空）单独成组
        Product(name="香港直播专线", product_type="直播线路", price=199,
                billing_days=30, region_id=us.id),
        # 停用商品不应出现
        Product(name="下架商品", product_type="独享静态住宅（普通）", price=10,
                billing_days=30, region_id=us.id, resource_category="static_residential", is_active=False),
    ])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield {"client": TestClient(app), "db": db}
    app.dependency_overrides.clear()
    rate_limiter.clear()


def test_catalog_groups_skus_by_category(api):
    response = api["client"].get("/api/public/catalog")
    assert response.status_code == 200, response.text
    body = response.json()
    codes = [item["code"] for item in body["categories"]]
    assert "static_residential" in codes
    assert any(code.startswith("fixed-") for code in codes)
    assert body["product_count"] == 5          # 停用商品不计
    assert body["region_count"] == 2

    residential = next(item for item in body["categories"] if item["code"] == "static_residential")
    assert residential["region_count"] == 2     # 美国 + 日本，而不是 4 条 SKU
    assert residential["min_price"] == 60.0     # 起价取最低周期价
    assert residential["periods"] == [30, 90]
    assert residential["in_stock"] is True
    assert "offers" not in residential["regions"][0]   # 列表不返回价格明细


def test_catalog_detail_returns_price_ladder(api):
    response = api["client"].get("/api/public/catalog/static_residential")
    assert response.status_code == 200, response.text
    body = response.json()
    regions = {item["name"]: item for item in body["regions"]}
    assert set(regions) == {"美国", "日本"}

    us_offers = sorted(regions["美国"]["offers"], key=lambda item: item["billing_days"])
    assert [offer["billing_days"] for offer in us_offers] == [30, 90]
    assert [offer["unit_price"] for offer in us_offers] == [60.0, 180.0]
    assert all(offer["in_stock"] for offer in us_offers)

    jp_offers = sorted(regions["日本"]["offers"], key=lambda item: item["billing_days"])
    # 日本 30 天有货、90 天停售：地区级 in_stock 取"任一有货"，档位级如实反映各自库存
    assert [offer["in_stock"] for offer in jp_offers] == [True, False]
    assert regions["日本"]["in_stock"] is True


def test_fixed_price_product_is_its_own_group(api):
    listing = api["client"].get("/api/public/catalog").json()
    fixed = next(item for item in listing["categories"] if item["kind"] == "fixed")
    detail = api["client"].get(f"/api/public/catalog/{fixed['code']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "直播线路"
    assert body["periods"] == [30]
    assert body["regions"][0]["offers"][0]["unit_price"] == 199.0


def test_unknown_category_is_404(api):
    assert api["client"].get("/api/public/catalog/not-exist").status_code == 404


def test_catalog_is_rate_limited(api):
    limit = __import__("app.core.config", fromlist=["Settings"]).Settings().public_rate_limit
    for _ in range(limit):
        assert api["client"].get("/api/public/catalog").status_code == 200
    assert api["client"].get("/api/public/catalog").status_code == 429
