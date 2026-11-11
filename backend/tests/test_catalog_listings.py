"""官网「产品线 × 国家」可见性：默认推导、人工覆盖、批量、商品级上下架。

对应 docs/商品上下架与销售端升级设计.md 第 1 节。
"""

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import AuditLog, CatalogListing, Customer, Product, Region, ResourcePool, RetailPriceRule, User
from app.models.base import Base
from app.services.registration_service import registration_rate_limiter
from app.services.session_service import create_session
from app.core.security import create_access_token


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    us = Region(name="美国", country_code="US")
    de = Region(name="德国", country_code="DE")
    fr = Region(name="法国", country_code="FR")  # 没有报价
    db.add_all([us, de, fr])
    db.flush()

    for category, code, price in (
        ("static_residential", "US", 60),
        ("social_media", "US", 95),
    ):
        db.add(RetailPriceRule(resource_category=category, country_code=code,
                               billing_days=30, unit_price=Decimal(price), source_version="test"))

    def add_product(region, category, price, active=True):
        db.add(Product(name=f"{region.name} {category} 30天", product_type=category, price=price,
                       billing_days=30, region_id=region.id, resource_category=category,
                       is_active=active, is_in_stock=True))

    add_product(us, "static_residential", 60)
    add_product(us, "social_media", 95)
    add_product(de, "static_residential", 85)  # 德国有住宅商品但没有价则，用来验证"有商品≠有报价"

    # 人工上架制：迁移会把当时在售的格子落成平台级 listed，这里模拟同样状态。
    pool_one = ResourcePool(code="global_proxy_pool", name="资源池 1", status="active",
                            strategy="priority_manual_fallback")
    pool_two = ResourcePool(code="global_proxy_pool_2", name="资源池 2", status="active",
                            strategy="priority_manual_fallback")
    db.add_all([pool_one, pool_two])
    db.flush()
    for line, region in (("residential", us), ("social", us), ("residential", de)):
        db.add(CatalogListing(pool_id=None, line_code=line, region_id=region.id,
                              status="listed", reason="测试回填"))

    admin = User(username="admin", password_hash=hash_password("Admin_123456"),
                 role="super_admin", display_name="管理员")
    sales = User(username="sales", password_hash=hash_password("Sales_123456"),
                 role="sales", display_name="销售")
    db.add_all([admin, sales])
    db.flush()
    customer_user = User(username="cust", password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户")
    db.add(customer_user)
    db.flush()
    db.add(Customer(name="客户一", email="c@test.com", user_id=customer_user.id))
    db.commit()

    admin_session, _ = create_session(db, admin)
    sales_session, _ = create_session(db, sales)
    customer_session, _ = create_session(db, customer_user)
    db.commit()
    headers = {
        "admin": {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, admin_session.id, admin.auth_version)}"},
        "sales": {"Authorization": f"Bearer {create_access_token(sales.id, sales.role, sales_session.id, sales.auth_version)}"},
        "customer": {"Authorization": f"Bearer {create_access_token(customer_user.id, customer_user.role, customer_session.id, customer_user.auth_version)}"},
    }
    state = {"db": db, "regions": {"US": us.id, "DE": de.id, "FR": fr.id}, "headers": headers,
             "pools": {"one": pool_one.id, "two": pool_two.id}}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    state["client"] = TestClient(app)
    yield state
    app.dependency_overrides.clear()
    registration_rate_limiter.clear()


def grid(api):
    response = api["client"].get("/api/admin/catalog-management/listings", headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    return response.json()


def cell(api, line_code, code):
    for item in grid(api)["cells"]:
        if item["line_code"] == line_code and item["country_code"] == code:
            return item
    raise AssertionError(f"格子里没有 {line_code}/{code}")


def catalog_countries(api, line_code="residential"):
    body = api["client"].get("/api/public/catalog").json()
    line = next(item for item in body["lines"] if item["code"] == line_code)
    return {region["country_code"] for region in line["regions"]}


def test_grid_is_manual_only_and_defaults_to_unlisted(api):
    body = grid(api)
    assert [line["code"] for line in body["lines"]] == ["residential", "social", "live"]
    # 三个启用国家 × 三条线
    assert body["summary"]["total"] == 9
    assert body["scope"]["pool_id"] is None

    # 美国住宅有商品且迁移时回填过平台级上架
    us = cell(api, "residential", "US")
    assert us["has_price"] is True
    assert us["status"] == "listed"
    assert us["source"] == "global"

    # 法国一点商品都没有、也没有上架记录 → 不可见
    fr = cell(api, "residential", "FR")
    assert fr["has_price"] is False
    assert fr["status"] == "unlisted"
    assert fr["source"] == "none"
    assert fr["visible"] is False

    # 德国有商品行但零售价目表没有它的价：has_price 只看商品存在与否
    de = cell(api, "residential", "DE")
    assert de["has_price"] is True

    assert body["summary"]["missing_price"] == 6  # FR 三条 + DE 的社媒/直播 + US 的直播


def test_new_priced_country_stays_hidden_until_listed(api):
    """新增国家/商品不会自动上线：没有上架记录就不出现，这是人工上架制的核心。"""
    # 给法国补一个真实商品 + 报价，但不上架
    de_region = api["regions"]["FR"]
    api["db"].add(Product(name="法国 静态住宅 30天", product_type="static_residential", price=88,
                          billing_days=30, region_id=de_region, resource_category="static_residential",
                          is_active=True, is_in_stock=True))
    api["db"].commit()
    assert "FR" not in catalog_countries(api)

    # 管理员点一次上架才出现
    response = api["client"].patch("/api/admin/catalog-management/listings",
                                   json={"line_code": "residential", "region_id": de_region,
                                         "status": "listed"},
                                   headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert "FR" in catalog_countries(api)


def test_pool_level_listing_only_affects_that_pool(api):
    fr = api["regions"]["FR"]
    pool_two = api["pools"]["two"]
    response = api["client"].patch("/api/admin/catalog-management/listings",
                                   json={"line_code": "social", "region_id": fr, "pool_id": pool_two,
                                         "status": "listed"},
                                   headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["pool_id"] == pool_two

    # 官网可见（任一池上架即对客户可见，具体由路由选池）
    assert "FR" in catalog_countries(api, "social")

    # 池 2 视图：本池记录优先
    pool_view = api["client"].get(f"/api/admin/catalog-management/listings?pool_id={pool_two}",
                                  headers=api["headers"]["admin"]).json()
    cell_pool = next(item for item in pool_view["cells"]
                     if item["line_code"] == "social" and item["country_code"] == "FR")
    assert cell_pool["status"] == "listed" and cell_pool["source"] == "pool"
    # 池 1 没有自己的记录，也没有平台级记录 → 不可见
    pool_one_view = api["client"].get(f"/api/admin/catalog-management/listings?pool_id={api['pools']['one']}",
                                      headers=api["headers"]["admin"]).json()
    cell_other = next(item for item in pool_one_view["cells"]
                      if item["line_code"] == "social" and item["country_code"] == "FR")
    assert cell_other["status"] == "unlisted" and cell_other["source"] == "none"


def test_pool_view_falls_back_to_platform_listing(api):
    """平台级上架对所有池生效：池视图里显示 source=global。"""
    pool_view = api["client"].get(f"/api/admin/catalog-management/listings?pool_id={api['pools']['two']}",
                                  headers=api["headers"]["admin"]).json()
    us = next(item for item in pool_view["cells"]
              if item["line_code"] == "residential" and item["country_code"] == "US")
    assert us["status"] == "listed" and us["source"] == "global"


def test_unknown_pool_is_rejected(api):
    assert api["client"].get("/api/admin/catalog-management/listings?pool_id=999",
                             headers=api["headers"]["admin"]).status_code == 404
    assert api["client"].patch("/api/admin/catalog-management/listings",
                               json={"line_code": "residential", "region_id": api["regions"]["US"],
                                     "pool_id": 999, "status": "listed"},
                               headers=api["headers"]["admin"]).status_code == 404


def test_unlisting_a_priced_country_hides_it_from_the_site(api):
    assert "US" in catalog_countries(api)

    response = api["client"].patch("/api/admin/catalog-management/listings",
                                   json={"line_code": "residential", "region_id": api["regions"]["US"],
                                         "status": "unlisted", "reason": "上游无货"},
                                   headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "global"

    assert "US" not in catalog_countries(api)
    # 其它线不受影响
    assert "US" in catalog_countries(api, "social")

    # 审计留痕
    audit = api["db"].scalar(select(AuditLog).where(AuditLog.action == "update_listing"))
    assert audit is not None and "US" in (audit.detail or "")


def test_listing_a_priceless_country_shows_it_as_no_quote(api):
    response = api["client"].patch("/api/admin/catalog-management/listings",
                                   json={"line_code": "social", "region_id": api["regions"]["FR"],
                                         "status": "listed"},
                                   headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text

    assert "FR" in catalog_countries(api, "social")
    body = api["client"].get("/api/catalog", headers=api["headers"]["customer"]).json()
    social = next(line for line in body["lines"] if line["code"] == "social")
    fr = next(region for region in social["regions"] if region["country_code"] == "FR")
    assert fr["min_price"] is None
    assert fr["offers"] == []
    assert fr["listed_by_admin"] is True


def test_manual_unlist_stays_after_the_price_comes_back(api):
    """人工下架是有意决定，补价不能让它自动复活。"""
    rid = api["regions"]["US"]
    api["client"].patch("/api/admin/catalog-management/listings",
                        json={"line_code": "residential", "region_id": rid, "status": "unlisted"},
                        headers=api["headers"]["admin"])
    assert "US" not in catalog_countries(api)

    # 补一条价则（模拟运营后来补价）
    api["db"].add(RetailPriceRule(resource_category="static_residential", country_code="US",
                                  billing_days=180, unit_price=Decimal(360), source_version="later"))
    api["db"].commit()

    assert "US" not in catalog_countries(api), "补价不应让手动下架的国家自动复活"

    # 管理员再点一次上架才恢复
    api["client"].patch("/api/admin/catalog-management/listings",
                        json={"line_code": "residential", "region_id": rid, "status": "listed"},
                        headers=api["headers"]["admin"])
    assert "US" in catalog_countries(api)


def test_bulk_update_covers_a_line(api):
    response = api["client"].post("/api/admin/catalog-management/listings/bulk",
                                  json={"line_code": "live", "region_ids": list(api["regions"].values()),
                                        "status": "listed"},
                                  headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 3
    for code in ("US", "DE", "FR"):
        assert cell(api, "live", code)["status"] == "listed"


def test_bulk_update_can_cover_every_line(api):
    response = api["client"].post("/api/admin/catalog-management/listings/bulk",
                                  json={"region_ids": [api["regions"]["FR"]], "status": "listed"},
                                  headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 3
    for line in ("residential", "social", "live"):
        assert cell(api, line, "FR")["status"] == "listed"


def test_invalid_status_and_unknown_region_are_rejected(api):
    bad = api["client"].patch("/api/admin/catalog-management/listings",
                              json={"line_code": "residential", "region_id": api["regions"]["US"],
                                    "status": "hidden"},
                              headers=api["headers"]["admin"])
    assert bad.status_code == 422

    missing = api["client"].patch("/api/admin/catalog-management/listings",
                                  json={"line_code": "residential", "region_id": 9999, "status": "listed"},
                                  headers=api["headers"]["admin"])
    assert missing.status_code == 404


def test_product_level_active_toggle(api):
    """商品级上下架：停用后整条商品从官网消失，不只是某个国家。"""
    body = api["client"].get("/api/admin/catalog-management/products?page=1&page_size=50",
                             headers=api["headers"]["admin"]).json()
    product = next(item for item in body["items"] if item["name"] == "美国 static_residential 30天")

    off = api["client"].patch(f"/api/admin/catalog-management/products/{product['id']}/active",
                              json={"is_active": False}, headers=api["headers"]["admin"])
    assert off.status_code == 200 and off.json()["changed"] is True
    assert "US" not in catalog_countries(api)

    again = api["client"].patch(f"/api/admin/catalog-management/products/{product['id']}/active",
                                json={"is_active": False}, headers=api["headers"]["admin"])
    assert again.json()["changed"] is False  # 幂等，不重复写审计

    on = api["client"].patch(f"/api/admin/catalog-management/products/{product['id']}/active",
                             json={"is_active": True}, headers=api["headers"]["admin"])
    assert on.json()["changed"] is True
    assert "US" in catalog_countries(api)


def test_sales_customer_cannot_manage_listings(api):
    payload = {"line_code": "residential", "region_id": api["regions"]["FR"], "status": "listed"}
    assert api["client"].patch("/api/admin/catalog-management/listings", json=payload,
                               headers=api["headers"]["sales"]).status_code == 403
    assert api["client"].patch("/api/admin/catalog-management/listings", json=payload,
                               headers=api["headers"]["customer"]).status_code == 403
    assert api["client"].get("/api/admin/catalog-management/listings",
                             headers=api["headers"]["sales"]).status_code == 403
