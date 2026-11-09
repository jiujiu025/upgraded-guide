"""官网目录接口：三条产品线、统一国家清单、未登录不含价格（2026-11-09 口径）。

产品线：静态住宅IP / 社媒运营专线 / 直播专线。
- 三条线的国家列表完全一致（全部启用地区），缺报价的国家显示为 null 而不是消失；
- 未登录（/public/catalog）不返回任何金额与 offer；
- 登录后（/catalog）返回起价、逐地区逐规格报价，价格全部来自数据库；
- 直播按规格（手机/电脑）拆分类，官网上合并到一条线里用 spec 区分。
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, Product, Region, RetailPriceRule, User
from app.models.base import Base
from app.services.registration_service import rate_limiter
from app.services.session_service import create_session


def add_product(db, region, category, label, price, billing_days=30, in_stock=True):
    db.add(Product(name=f"{region.name} {label} {billing_days}天", product_type=label, price=price,
                   billing_days=billing_days, region_id=region.id,
                   resource_category=category, is_in_stock=in_stock, is_active=True))


@pytest.fixture()
def api():
    rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    us = Region(name="美国", country_code="US")
    jp = Region(name="日本", country_code="JP")
    de = Region(name="德国", country_code="DE")
    fr = Region(name="法国", country_code="FR")   # 一点报价都没有的国家
    db.add_all([us, jp, de, fr])
    db.flush()
    for category, country_code, price in (
        ("static_residential", "US", 60), ("static_residential", "JP", 85),
        ("social_media", "US", 95), ("social_media", "JP", 123),
        ("live_stream_mobile", "US", 600), ("live_stream_pc", "US", 1100),
    ):
        db.add(RetailPriceRule(resource_category=category, country_code=country_code,
                               billing_days=30, unit_price=Decimal(price), source_version="test"))
    # 美国、日本有住宅价；德国故意没有 → 仍应出现在列表里，价格为 null
    add_product(db, us, "static_residential", "静态住宅IP", 60)
    add_product(db, jp, "static_residential", "静态住宅IP", 85)
    add_product(db, us, "social_media", "社媒运营专线", 95)
    add_product(db, jp, "social_media", "社媒运营专线", 123)
    add_product(db, us, "live_stream_mobile", "直播专线（手机）", 600)
    add_product(db, us, "live_stream_pc", "直播专线（电脑）", 1100)
    # 固定价老商品不应出现在官网三条线里
    db.add(Product(name="香港直播专线", product_type="直播线路", price=199, billing_days=30,
                   region_id=us.id, is_active=True, is_in_stock=True))
    # 停用商品不出现
    add_product(db, de, "static_residential", "静态住宅IP", 85, in_stock=False)

    customer_user = User(username="cust", password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户")
    db.add(customer_user)
    db.flush()
    db.add(Customer(name="客户一", email="c@test.com", user_id=customer_user.id))
    db.commit()
    session_row, _ = create_session(db, customer_user)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(customer_user.id, customer_user.role, session_row.id, customer_user.auth_version)}"}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield {"client": TestClient(app), "headers": headers, "db": db}
    app.dependency_overrides.clear()
    rate_limiter.clear()


def test_public_catalog_has_three_lines_and_no_prices(api):
    response = api["client"].get("/api/public/catalog")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["price_visible"] is False
    codes = [line["code"] for line in body["lines"]]
    assert codes == ["residential", "social", "live"]
    for line in body["lines"]:
        # 三条线的国家列表必须一致，且包含没有报价的国家（德国）
        assert line["region_count"] == 4
        assert {region["name"] for region in line["regions"]} == {"美国", "日本", "德国", "法国"}
        assert line["min_price"] is None
        for region in line["regions"]:
            assert region["min_price"] is None
            assert region["offers"] == []


def test_public_catalog_detail_hides_price_too(api):
    response = api["client"].get("/api/public/catalog/residential")
    assert response.status_code == 200
    body = response.json()
    assert body["min_price"] is None
    assert all(not region["offers"] for region in body["regions"])
    assert body["periods"] == [30, 90, 180, 360]


def test_signed_in_catalog_exposes_prices(api):
    response = api["client"].get("/api/catalog", headers=api["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["price_visible"] is True
    lines = {line["code"]: line for line in body["lines"]}
    assert lines["residential"]["min_price"] == 60.0
    residential_regions = {region["name"]: region for region in lines["residential"]["regions"]}
    assert residential_regions["美国"]["min_price"] == 60.0
    assert residential_regions["日本"]["min_price"] == 85.0
    # 一点报价都没有的国家仍在列表里，价格为 null
    assert residential_regions["法国"]["min_price"] is None
    assert residential_regions["法国"]["offers"] == []
    # 有商品但停售：价格仍然展示，国家标记为不可下单
    assert residential_regions["德国"]["min_price"] == 85.0
    assert residential_regions["德国"]["in_stock"] is False


def test_live_line_merges_two_specs(api):
    line = api["client"].get("/api/catalog/live", headers=api["headers"]).json()
    assert line["min_price"] == 600.0
    us = next(region for region in line["regions"] if region["name"] == "美国")
    offers = {offer["spec"]: offer["unit_price"] for offer in us["offers"]}
    assert offers == {"手机": 600.0, "电脑": 1100.0}


def test_unknown_line_is_404(api):
    assert api["client"].get("/api/public/catalog/not-exist").status_code == 404


def test_catalog_requires_auth_for_prices(api):
    # 未登录访问登录版接口必须 401，不能靠前端藏
    assert api["client"].get("/api/catalog").status_code == 401
