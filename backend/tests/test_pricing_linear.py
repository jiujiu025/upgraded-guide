"""定价口径回归：严格线性、无折扣（口径见 docs/定价收敛实施方案.md）。"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Product, Region, RetailPriceRule, User
from app.models.base import Base
from app.services import pricing_service
from app.services.pricing_service import PricingError
from app.services.session_service import create_session

LEGACY_PRODUCT_TYPE = "直播线路"


@pytest.fixture()
def pricing_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    us = Region(name="美国", country_code="US")
    db.add(us)
    db.flush()

    # 价目表：月价 ¥60；同时故意留一条"旧的打折价"180 天 = ¥300，验证它不再生效
    db.add_all([
        RetailPriceRule(resource_category="static_residential", country_code="US",
                        billing_days=30, unit_price=Decimal("60"), source_version="test"),
        RetailPriceRule(resource_category="static_residential", country_code="US",
                        billing_days=180, unit_price=Decimal("300"), source_version="test"),
    ])

    monthly_product = Product(name="美国 独享静态住宅 30天", product_type="独享静态住宅（普通）",
                              price=60, billing_days=30, region_id=us.id,
                              resource_category="static_residential")
    long_product = Product(name="美国 独享静态住宅 180天", product_type="独享静态住宅（普通）",
                            price=300, billing_days=180, region_id=us.id,
                            resource_category="static_residential")
    legacy_product = Product(name="香港直播专线", product_type=LEGACY_PRODUCT_TYPE,
                             price=199, billing_days=30, region_id=us.id)
    db.add_all([monthly_product, long_product, legacy_product])
    admin = User(username="pricing_admin", password_hash=hash_password("Admin_123456"),
                 role="admin", display_name="定价管理员")
    db.add(admin)
    db.commit()
    admin_session, _ = create_session(db, admin)
    db.commit()
    admin_header = {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, admin_session.id, admin.auth_version)}"}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"db": db, "client": client, "monthly": monthly_product,
           "long": long_product, "legacy": legacy_product, "region": us, "admin_header": admin_header}

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_periods_are_linear(pricing_env):
    periods = pricing_service.periods_of(pricing_env["db"], pricing_env["monthly"])
    assert [p["billing_days"] for p in periods] == [30, 90, 180, 360]
    assert [p["unit_price"] for p in periods] == [60.0, 180.0, 360.0, 720.0]


def test_stale_long_period_row_is_ignored(pricing_env):
    """库里那条 180 天 = ¥300 的旧打折价不得再生效。"""
    unit, total = pricing_service.quote(pricing_env["db"], pricing_env["monthly"], 1, 180)
    assert unit == Decimal("360.00")
    assert total == Decimal("360.00")


def test_quote_unit_times_quantity_equals_total(pricing_env):
    unit, total = pricing_service.quote(pricing_env["db"], pricing_env["monthly"], 2, 180)
    assert unit == Decimal("360.00")
    assert total == Decimal("720.00")


def test_quarter_period_is_sellable(pricing_env):
    unit, total = pricing_service.quote(pricing_env["db"], pricing_env["monthly"], 1, 90)
    assert unit == Decimal("180.00")
    assert total == Decimal("180.00")


@pytest.mark.parametrize("days", [45, 60, 120, 365])
def test_unsupported_period_rejected(pricing_env, days):
    with pytest.raises(PricingError):
        pricing_service.quote(pricing_env["db"], pricing_env["monthly"], 1, days)


def test_legacy_fixed_price_product_unchanged(pricing_env):
    db, product = pricing_env["db"], pricing_env["legacy"]
    assert pricing_service.quote(db, product, 1, 30) == (Decimal("199.00"), Decimal("199.00"))
    unit, total = pricing_service.quote(db, product, 1, 180)
    assert unit == Decimal("1094.50")            # 199 × 5.5，老逻辑不动
    assert total == Decimal("1094.50")
    assert pricing_service.periods_of(db, product) == [{"billing_days": 30, "unit_price": 199.0}]


def test_long_period_product_only_sells_its_own_period(pricing_env):
    db, product = pricing_env["db"], pricing_env["long"]
    assert [p["billing_days"] for p in pricing_service.periods_of(db, product)] == [180]
    with pytest.raises(PricingError):
        pricing_service.quote(db, product, 1, 30)


def test_display_price_follows_monthly_rule(pricing_env):
    db, product = pricing_env["db"], pricing_env["long"]
    assert pricing_service.display_price_of(db, product) == 360.0   # 不是 300
    assert pricing_service.sync_display_price(db, product) is True
    assert product.price == 360.0
    assert pricing_service.sync_display_price(db, product) is False  # 幂等


def test_missing_monthly_rule_means_not_sellable(pricing_env):
    db = pricing_env["db"]
    orphan = Product(name="无价目商品", product_type="独享静态数据中心", price=45,
                     billing_days=30, region_id=pricing_env["region"].id,
                     resource_category="static_datacenter")
    db.add(orphan)
    db.commit()
    assert pricing_service.periods_of(db, orphan) == []
    with pytest.raises(PricingError):
        pricing_service.quote(db, orphan, 1, 30)


def test_preview_api_is_self_consistent_and_has_no_discount(pricing_env):
    client, product = pricing_env["client"], pricing_env["monthly"]
    response = client.post("/api/public/orders/preview", json={
        "product_id": product.id, "region_id": product.region_id,
        "quantity": 2, "billing_days": 180,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert "discount" not in body
    assert body["unit_price"] == 360.0
    assert body["total_amount"] == 720.0
    assert body["unit_price"] * body["quantity"] == body["total_amount"]
    assert [p["billing_days"] for p in body["periods"]] == [30, 90, 180, 360]


def test_public_products_expose_periods(pricing_env):
    client = pricing_env["client"]
    rows = client.get("/api/public/products").json()
    monthly = next(item for item in rows if item["name"] == "美国 独享静态住宅 30天")
    assert monthly["resource_category"] == "static_residential"
    assert [p["unit_price"] for p in monthly["periods"]] == [60.0, 180.0, 360.0, 720.0]
    legacy = next(item for item in rows if item["name"] == "香港直播专线")
    assert legacy["periods"] == [{"billing_days": 30, "unit_price": 199.0}]


def test_admin_cannot_edit_locked_product_price(pricing_env):
    client, product, headers = pricing_env["client"], pricing_env["monthly"], pricing_env["admin_header"]
    response = client.patch(f"/api/admin/catalog-management/products/{product.id}",
                            json={"price": 88}, headers=headers)
    assert response.status_code == 400
    assert "零售价" in response.json()["detail"]


def test_admin_can_still_toggle_stock_of_locked_product(pricing_env):
    client, product, headers = pricing_env["client"], pricing_env["monthly"], pricing_env["admin_header"]
    response = client.patch(f"/api/admin/catalog-management/products/{product.id}",
                            json={"is_in_stock": False}, headers=headers)
    assert response.status_code == 200
    assert response.json()["is_in_stock"] is False


def test_admin_cannot_edit_long_period_retail_price(pricing_env):
    client, db, headers = pricing_env["client"], pricing_env["db"], pricing_env["admin_header"]
    rule = db.scalars(select(RetailPriceRule).where(RetailPriceRule.billing_days == 180)).one()
    response = client.patch(f"/api/admin/catalog-management/retail-prices/{rule.id}",
                            json={"unit_price": 300}, headers=headers)
    assert response.status_code == 400
    assert "30 天" in response.json()["detail"]


def test_monthly_price_update_syncs_product_display_price(pricing_env):
    client, db, headers = pricing_env["client"], pricing_env["db"], pricing_env["admin_header"]
    rule = db.scalars(select(RetailPriceRule).where(RetailPriceRule.billing_days == 30)).one()
    response = client.patch(f"/api/admin/catalog-management/retail-prices/{rule.id}",
                            json={"unit_price": 88}, headers=headers)
    assert response.status_code == 200, response.text
    db.expire_all()
    products = db.scalars(select(Product).where(Product.resource_category == "static_residential")).all()
    assert {product.billing_days: product.price for product in products} == {30: 88.0, 180: 528.0}
    # 长周期价继续按新倍率派生，不受库里旧的休眠行影响
    assert pricing_service.quote(db, pricing_env["monthly"], 1, 360) == (Decimal("1056.00"), Decimal("1056.00"))
