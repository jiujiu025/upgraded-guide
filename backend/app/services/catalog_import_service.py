"""一键把供应商国家目录落成「可售 SKU」：商品 + 月价 + 供给规则 + 官网上架。

为什么单独一个服务，而不是继续扩 ``sync_supplier_catalog``：

- 旧同步只建商品，缺价的直接跳过，建完还是「无库存 + 未上架」，运营要再点三四个页面；
- 「可售」是一个整体状态，拆成几次点击就会出现「商品建了、价没配、官网看不见」的中间态。

这里的口径（与 docs/定价收敛实施方案.md 一致）：

1. 一个（分类, 国家）只建 **30 天** 一个 SKU；90/180/360 天由月价线性派生，不再建重复商品；
2. 月价只认 ``RetailPriceRule`` 的 30 天档；本次可补价，但**不覆盖**已有月价（除非显式要求）；
3. 可售 = 商品启用 + 有价 + 供给规则启用 + 官网可见性上架，四件事一次做完；
4. 幂等：重复执行只补差异，不产生重复商品 / 重复规则 / 重复上架记录。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.site_catalog import CATEGORY_LABELS, CATEGORY_TO_LINE
from app.models import (
    CatalogListing,
    Product,
    ProductSupplyRule,
    Region,
    RetailPriceRule,
    SupplierProduct,
    SupplierRegion,
    User,
)
from app.services.pricing_service import BASE_PERIOD_DAYS, unit_price_for
from app.services.supplier_service import (
    PUBLISHABLE_PRODUCT_CATEGORIES,
    ensure_pool_one,
    utcnow,
)

CENT = Decimal("0.01")


class CatalogImportError(ValueError):
    """参数问题，路由层映射成 4xx。"""


@dataclass
class ImportOutcome:
    dry_run: bool
    supplier_products: int = 0
    countries: int = 0
    products_created: int = 0
    products_updated: int = 0
    prices_created: int = 0
    prices_updated: int = 0
    rules_enabled: int = 0
    listings_listed: int = 0
    skipped: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "supplier_products": self.supplier_products,
            "countries": self.countries,
            "products_created": self.products_created,
            "products_updated": self.products_updated,
            "prices_created": self.prices_created,
            "prices_updated": self.prices_updated,
            "rules_enabled": self.rules_enabled,
            "listings_listed": self.listings_listed,
            "skipped": self.skipped[:50],
            "skipped_total": len(self.skipped),
            "items": self.items[:100],
            "item_total": len(self.items),
        }


def _find_region(db: Session, country_code: str) -> Region | None:
    return db.scalar(select(Region).where(Region.country_code == country_code)
                     .order_by(Region.is_active.desc(), Region.id).limit(1))


def _ensure_region(db: Session, country_code: str, country_name: str) -> Region:
    region = _find_region(db, country_code)
    if region:
        return region
    region = Region(name=(country_name.strip() or country_code)[:80], country_code=country_code, is_active=True)
    db.add(region)
    db.flush()
    return region


def _monthly_price(db: Session, category: str, country_code: str) -> RetailPriceRule | None:
    return db.scalar(select(RetailPriceRule).where(
        RetailPriceRule.resource_category == category,
        RetailPriceRule.country_code == country_code,
        RetailPriceRule.billing_days == BASE_PERIOD_DAYS,
    ))


def _resolve_price(db: Session, category: str, country_code: str, *,
                   default_price: Decimal | None, country_prices: dict[str, Decimal],
                   overwrite: bool) -> tuple[RetailPriceRule | None, Decimal | None, bool]:
    """返回 (规则, 本次要写入的月价, 是否新建)。None 表示缺价、必须跳过。"""
    rule = _monthly_price(db, category, country_code)
    provided = country_prices.get(country_code) or default_price
    if rule is not None:
        if overwrite and provided is not None and Decimal(str(rule.unit_price)) != provided:
            return rule, provided, False
        return rule, None, False
    if provided is None:
        return None, None, False
    return None, provided.quantize(CENT, rounding=ROUND_HALF_UP), True


def _find_product(db: Session, region_id: int, category: str) -> Product | None:
    """优先找已归类的 30 天 SKU；没有就认领同国家的旧固定价 30 天商品。

    旧数据里 ``resource_category`` 可能为空（固定价商品），如果直接按分类查不到就新建，
    同一个国家会出现两个 30 天商品。这里把旧的认领过来，再由调用方回填分类与价格。
    """
    product = db.scalar(select(Product).where(
        Product.region_id == region_id,
        Product.billing_days == BASE_PERIOD_DAYS,
        Product.resource_category == category,
    ).order_by(Product.id).limit(1))
    if product is not None:
        return product
    # 只认领“本来就是这条产品线”的旧商品（product_type 与分类标签一致）。
    # 不能按 region + 30 天盲认领：同一个国家可能还有直播专线等固定价商品，
    # 把它们的 resource_category 改成静态住宅会把另一条线卖成静态 IP。
    label = CATEGORY_LABELS.get(category, "")
    return db.scalar(select(Product).where(
        Product.region_id == region_id,
        Product.billing_days == BASE_PERIOD_DAYS,
        Product.resource_category.is_(None),
        Product.product_type == label,
    ).order_by(Product.id).limit(1))


def _upsert_listing(db: Session, *, line_code: str, region_id: int, actor: User | None) -> bool:
    """平台级（pool_id 为空）上架。返回是否新写了记录。"""
    record = db.scalar(select(CatalogListing).where(
        CatalogListing.line_code == line_code,
        CatalogListing.region_id == region_id,
        CatalogListing.pool_id.is_(None),
    ))
    if record is None:
        db.add(CatalogListing(line_code=line_code, region_id=region_id, pool_id=None,
                              status="listed", reason="一键落成可售 SKU",
                              updated_by=actor.id if actor else None))
        db.flush()
        return True
    if record.status != "listed" or record.reason != "一键落成可售 SKU":
        record.status = "listed"
        record.reason = "一键落成可售 SKU"
        record.updated_by = actor.id if actor else None
        db.flush()
        return True
    return False


def import_sellable_catalog(
    db: Session,
    actor: User | None,
    *,
    supplier_product_ids: list[int] | None = None,
    default_monthly_price: Decimal | None = None,
    country_monthly_prices: dict[str, Decimal] | None = None,
    overwrite_prices: bool = False,
    enable_supply_rules: bool = True,
    list_on_site: bool = True,
    dry_run: bool = True,
) -> ImportOutcome:
    """按供应商国家目录生成可售 SKU。``dry_run=True`` 只算差异、不写库。"""
    if default_monthly_price is not None and default_monthly_price <= 0:
        raise CatalogImportError("默认月价必须大于 0")
    country_prices = {code.strip().upper(): Decimal(str(value))
                      for code, value in (country_monthly_prices or {}).items()}
    if any(value <= 0 for value in country_prices.values()):
        raise CatalogImportError("国家月价必须大于 0")

    supplier, pool = ensure_pool_one(db)
    query = select(SupplierProduct).where(
        SupplierProduct.supplier_id == supplier.id,
        SupplierProduct.is_available.is_(True),
        SupplierProduct.external_product_ref.in_(PUBLISHABLE_PRODUCT_CATEGORIES),
    )
    if supplier_product_ids:
        query = query.where(SupplierProduct.id.in_(supplier_product_ids))
    supplier_products = list(db.scalars(query.order_by(SupplierProduct.id)).all())
    if not supplier_products:
        raise CatalogImportError("没有可导入的供应商商品，请先同步供应商目录")

    outcome = ImportOutcome(dry_run=dry_run, supplier_products=len(supplier_products))
    seen_countries: set[tuple[str, str]] = set()

    for supplier_product in supplier_products:
        category = PUBLISHABLE_PRODUCT_CATEGORIES.get(supplier_product.external_product_ref)
        if not category:
            continue
        supplier_product.resource_category = category
        regions = db.scalars(select(SupplierRegion).where(
            SupplierRegion.supplier_product_id == supplier_product.id,
            SupplierRegion.city_name.is_(None),
            SupplierRegion.is_available.is_(True),
        ).order_by(SupplierRegion.id)).all()
        for supplier_region in regions:
            country = (supplier_region.country_code or "").strip().upper()
            if not country:
                continue
            if (category, country) in seen_countries:
                continue
            seen_countries.add((category, country))
            outcome.countries += 1

            rule, price_to_write, price_is_new = _resolve_price(
                db, category, country,
                default_price=default_monthly_price, country_prices=country_prices,
                overwrite=overwrite_prices,
            )
            if rule is None and price_to_write is None:
                outcome.skipped.append({"country": country, "reason": "missing_price"})
                continue
            monthly = price_to_write if price_to_write is not None else Decimal(str(rule.unit_price))
            if price_to_write is not None and not dry_run:
                # 价目表的长周期行由 30 天档派生，不再单独写；只落月价。
                if price_is_new:
                    rule = RetailPriceRule(
                        resource_category=category, country_code=country,
                        billing_days=BASE_PERIOD_DAYS, unit_price=monthly, currency="CNY",
                        source_version=f"import-{utcnow():%Y%m%d}",
                    )
                    db.add(rule)
                    db.flush()
                    outcome.prices_created += 1
                else:
                    rule.unit_price = monthly
                    rule.source_version = f"import-{utcnow():%Y%m%d}"
                    outcome.prices_updated += 1
            elif price_to_write is not None:
                outcome.prices_created += 1 if price_is_new else 0
                outcome.prices_updated += 0 if price_is_new else 1

            region = _find_region(db, country) if dry_run else _ensure_region(
                db, country, supplier_region.country_name)
            if region is None:
                # 预演时本地还没有这个国家（首次导入很常见）：按“会新建地区 + 商品 + 规则 + 上架”计入，
                # 否则预演结果会比实际执行少一截，运营无法据此判断要写多少数据。
                outcome.products_created += 1
                outcome.rules_enabled += 1 if enable_supply_rules else 0
                outcome.listings_listed += 1 if list_on_site else 0
                outcome.items.append({
                    "country": country,
                    "monthly_price": float(monthly),
                    "product_action": "create",
                })
                continue

            expected_price = float(unit_price_for(monthly, BASE_PERIOD_DAYS))
            product = _find_product(db, region.id, category)
            if product is None:
                outcome.products_created += 1
                if not dry_run:
                    product = Product(
                        name=f"{region.name} {CATEGORY_LABELS.get(category, category)} {BASE_PERIOD_DAYS}天",
                        product_type=CATEGORY_LABELS.get(category, category),
                        price=expected_price,
                        billing_days=BASE_PERIOD_DAYS,
                        region_id=region.id,
                        resource_category=category,
                        is_active=True,
                        is_in_stock=True,
                    )
                    db.add(product)
                    db.flush()
            else:
                changed = False
                if product.resource_category != category:
                    product.resource_category = category
                    changed = True
                if abs(float(product.price) - expected_price) >= 0.005:
                    product.price = expected_price
                    changed = True
                if not product.is_active:
                    product.is_active = True
                    changed = True
                if not product.is_in_stock:
                    product.is_in_stock = True
                    changed = True
                if changed:
                    outcome.products_updated += 1

            if not dry_run and product is not None:
                rule_record = db.scalar(select(ProductSupplyRule).where(
                    ProductSupplyRule.product_id == product.id,
                    ProductSupplyRule.region_id == region.id,
                    ProductSupplyRule.pool_id == pool.id,
                ))
                if rule_record is None:
                    rule_record = ProductSupplyRule(
                        product_id=product.id, region_id=region.id, pool_id=pool.id,
                        supplier_product_id=supplier_product.id, supplier_region_id=supplier_region.id,
                        mode="manual", is_enabled=enable_supply_rules,
                    )
                    db.add(rule_record)
                    db.flush()
                    if enable_supply_rules:
                        outcome.rules_enabled += 1
                else:
                    rule_record.supplier_product_id = supplier_product.id
                    rule_record.supplier_region_id = supplier_region.id
                    if rule_record.is_enabled != enable_supply_rules:
                        rule_record.is_enabled = enable_supply_rules
                        if enable_supply_rules:
                            outcome.rules_enabled += 1

                if list_on_site and _upsert_listing(
                    db, line_code=CATEGORY_TO_LINE[category], region_id=region.id, actor=actor,
                ):
                    outcome.listings_listed += 1

            outcome.items.append({
                "country": country,
                "monthly_price": float(monthly),
                "product_action": "create" if product is None else "update",
            })

    if not dry_run:
        db.flush()
    return outcome
