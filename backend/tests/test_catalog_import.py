"""一键落成可售 SKU：商品 + 月价 + 供给规则 + 官网上架。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    CatalogListing, Product, ProductSupplyRule, RetailPriceRule, SupplierProduct, SupplierRegion,
)
from app.models.base import Base
from app.services.catalog_import_service import import_sellable_catalog
from app.services.supplier_service import ensure_pool_one


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, Session()


def seed_supplier(db, countries=(("US", "美国"), ("DE", "德国"))):
    supplier, pool = ensure_pool_one(db)
    product = SupplierProduct(
        supplier_id=supplier.id, external_product_ref="3", name_snapshot="原生静态住宅",
        resource_category="static_residential", currency="CNY", is_available=True,
        source_endpoint="/externalapi/conpons/getProductList", last_synced_at=datetime(2026, 11, 12),
    )
    db.add(product)
    db.flush()
    for code, name in countries:
        db.add(SupplierRegion(
            supplier_product_id=product.id, external_country_ref=f"ref-{code}", country_code=code,
            country_name=name, external_city_ref="", city_name=None, is_available=True,
            source_endpoint="/externalapi/inventory/countryList", last_synced_at=datetime(2026, 11, 12),
        ))
    db.add(RetailPriceRule(
        resource_category="static_residential", country_code="US", billing_days=30,
        unit_price=Decimal("60.00"), currency="CNY", source_version="seed",
    ))
    db.commit()
    return product


def test_dry_run_writes_nothing():
    engine, db = make_session()
    try:
        seed_supplier(db)
        result = import_sellable_catalog(db, None, default_monthly_price=Decimal("70"), dry_run=True)
        db.rollback()

        assert result.products_created == 2
        assert result.prices_created == 1
        assert db.scalar(select(func.count()).select_from(Product)) == 0
        assert db.scalar(select(func.count()).select_from(RetailPriceRule)) == 1
        assert db.scalar(select(func.count()).select_from(CatalogListing)) == 0
    finally:
        db.close()
        engine.dispose()


def test_apply_creates_sellable_skus_and_is_idempotent():
    engine, db = make_session()
    try:
        seed_supplier(db)
        result = import_sellable_catalog(
            db, None, default_monthly_price=Decimal("70"),
            enable_supply_rules=True, list_on_site=True, dry_run=False,
        )
        db.commit()

        assert result.countries == 2
        assert result.products_created == 2
        assert result.prices_created == 1  # 只有 DE 缺价
        assert result.rules_enabled == 2
        assert result.listings_listed == 2
        assert result.skipped == []

        us = db.scalar(select(Product).where(Product.name.like("美国%"))).price
        de = db.scalar(select(Product).where(Product.name.like("德国%"))).price
        assert float(us) == 60.0       # 已有月价不被默认价覆盖
        assert float(de) == 70.0
        assert db.scalar(select(func.count()).select_from(ProductSupplyRule)) == 2
        assert db.scalar(select(func.count()).select_from(CatalogListing)) == 2

        again = import_sellable_catalog(
            db, None, default_monthly_price=Decimal("70"), dry_run=False,
        )
        db.commit()
        assert again.products_created == 0
        assert again.prices_created == 0
        assert again.listings_listed == 0
        assert db.scalar(select(func.count()).select_from(Product)) == 2
        assert db.scalar(select(func.count()).select_from(CatalogListing)) == 2
    finally:
        db.close()
        engine.dispose()


def test_missing_price_is_skipped_not_defaulted_to_zero():
    engine, db = make_session()
    try:
        seed_supplier(db, countries=(("DE", "德国"),))
        result = import_sellable_catalog(db, None, dry_run=False)
        db.commit()

        assert result.products_created == 0
        assert result.skipped == [{"country": "DE", "reason": "missing_price"}]
        assert db.scalar(select(func.count()).select_from(Product)) == 0
    finally:
        db.close()
        engine.dispose()


def test_overwrite_price_updates_rule_and_display_price():
    engine, db = make_session()
    try:
        seed_supplier(db, countries=(("US", "美国"),))
        first = import_sellable_catalog(db, None, dry_run=False)
        db.commit()
        assert first.products_created == 1

        result = import_sellable_catalog(
            db, None, default_monthly_price=Decimal("88"), overwrite_prices=True, dry_run=False,
        )
        db.commit()

        rule = db.scalar(select(RetailPriceRule).where(
            RetailPriceRule.resource_category == "static_residential",
            RetailPriceRule.country_code == "US",
            RetailPriceRule.billing_days == 30,
        ))
        product = db.scalar(select(Product).where(Product.resource_category == "static_residential"))
        assert float(rule.unit_price) == 88.0
        assert float(product.price) == 88.0
        assert result.prices_updated == 1
        assert result.products_updated == 1
    finally:
        db.close()
        engine.dispose()


def test_does_not_convert_fixed_price_product_from_another_line():
    """同一国家已有直播专线固定价商品时，不能把它认领成静态住宅 SKU。"""
    from app.models import Region

    engine, db = make_session()
    try:
        seed_supplier(db, countries=(("HK", "中国香港"),))
        region = Region(name="中国香港", country_code="HK")
        db.add(region)
        db.flush()
        db.add(Product(name="香港直播专线", product_type="直播线路", price=199,
                       billing_days=30, region_id=region.id, resource_category=None,
                       is_active=True, is_in_stock=True))
        db.add(RetailPriceRule(resource_category="static_residential", country_code="HK",
                               billing_days=30, unit_price=Decimal("60.00"), currency="CNY",
                               source_version="seed"))
        db.commit()

        result = import_sellable_catalog(db, None, dry_run=False)
        db.commit()

        assert result.products_created == 1
        fixed = db.scalar(select(Product).where(Product.product_type == "直播线路"))
        assert fixed.resource_category is None and float(fixed.price) == 199.0
    finally:
        db.close()
        engine.dispose()
