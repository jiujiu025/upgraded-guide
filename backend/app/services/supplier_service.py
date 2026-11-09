from datetime import datetime, timezone
from decimal import Decimal
from ipaddress import ip_network

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.site_catalog import CATEGORY_LABELS
from app.models import (
    Product, ProductSupplyRule, Region, ResourcePool, ResourcePoolSupplier, RetailPriceRule, Supplier,
    SupplierInventorySnapshot, SupplierIpSegmentSnapshot, SupplierProduct, SupplierQuote, SupplierRegion, SupplierSyncRun, User,
)
from app.services.audit_service import write_audit
from app.suppliers.base import InventoryItem, QuoteItem, SupplierInvalidResponseError, SupplierProductItem, SupplierRegionItem

SUPPLIER_CODE = "quanqiudaili"
POOL_CODE = "global_proxy_pool"
PRODUCTS_ENDPOINT = "/externalapi/conpons/getProductList"
COUNTRIES_ENDPOINT = "/externalapi/inventory/countryList"
CITIES_ENDPOINT = "/externalapi/inventory/cityList"
INVENTORY_ENDPOINT = "/externalapi/inventory/getBuyProductSku"
INVENTORY_CHECK_ENDPOINT = "/externalapi/product_order/checkInventory"
QUOTES_ENDPOINT = "/externalapi/product_price/getProductUnitPrice"
RESOURCE_CATEGORY_CODES = {
    # 官网在售的那条住宅线。业务口径：我们卖的静态住宅就是运营商原生 IP（ISP 原生），
    # 不是机房伪装住宅，所以分类名直接写清楚，避免后台看到"普通"以为是数据中心改的。
    "static_residential": "静态住宅原生IP",
    "static_residential_native": "独享静态住宅（本土原生）",
    "static_residential_isp": "独享静态住宅（运营商原生）",
    "static_datacenter": "独享静态数据中心",
    # 以下三类是自有产品线（不打供应商库存），但共用同一张零售价表，
    # 所以也放进这个映射里，后台「商品与价格」的分类下拉才能选到它们。
    # 供应商采购下单只认上面四种静态住宅细分，不会误用这几个。
    "social_media": "社媒运营专线",
    "live_stream_mobile": "直播专线（手机）",
    "live_stream_pc": "直播专线（电脑）",
}
PUBLISHABLE_PRODUCT_CATEGORIES = {
    "2": "static_residential",
    "3": "static_residential_native",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_pool_one(db: Session) -> tuple[Supplier, ResourcePool]:
    supplier = db.scalar(select(Supplier).where(Supplier.code == SUPPLIER_CODE))
    if not supplier:
        supplier = Supplier(
            code=SUPPLIER_CODE,
            name="全球代理",
            adapter_type="global_proxy_readonly",
            base_url="https://admin.quanqiudaili.com",
            secret_ref="env:GLOBAL_PROXY_API_KEY",
            status="active",
            auto_purchase_enabled=False,
        )
        db.add(supplier)
        db.flush()
    pool = db.scalar(select(ResourcePool).where(ResourcePool.code == POOL_CODE))
    if not pool:
        pool = ResourcePool(code=POOL_CODE, name="资源池 1", status="active", strategy="priority_manual_fallback")
        db.add(pool)
        db.flush()
    member = db.scalar(select(ResourcePoolSupplier).where(
        ResourcePoolSupplier.pool_id == pool.id,
        ResourcePoolSupplier.supplier_id == supplier.id,
    ))
    if not member:
        db.add(ResourcePoolSupplier(pool_id=pool.id, supplier_id=supplier.id, priority=1, is_enabled=True))
    return supplier, pool


def begin_sync_run(db: Session, actor: User, sync_type: str) -> SupplierSyncRun:
    supplier, _ = ensure_pool_one(db)
    run = SupplierSyncRun(
        supplier_id=supplier.id,
        actor_id=actor.id,
        sync_type=sync_type,
        status="running",
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    write_audit(db, actor, "sync_start", "supplier_sync_run", run.id, f"类型={sync_type}; 供应商={SUPPLIER_CODE}")
    db.commit()
    db.refresh(run)
    return run


def finish_sync_run(
    db: Session,
    actor: User,
    run_id: int,
    status: str,
    items_seen: int = 0,
    items_changed: int = 0,
    error_code: str | None = None,
    error_message: str | None = None,
) -> SupplierSyncRun:
    run = db.get(SupplierSyncRun, run_id)
    if not run:
        raise ValueError("同步记录不存在")
    run.status = status
    run.items_seen = items_seen
    run.items_changed = items_changed
    run.error_code = error_code
    run.error_message = error_message[:255] if error_message else None
    run.finished_at = utcnow()
    write_audit(
        db,
        actor,
        "sync_finish",
        "supplier_sync_run",
        run.id,
        f"类型={run.sync_type}; 状态={status}; 数量={items_changed}; 错误码={error_code or '无'}",
    )
    db.commit()
    db.refresh(run)
    return run


def sync_products(
    db: Session, items: list[SupplierProductItem], synced_at: datetime | None = None,
) -> list[SupplierProduct]:
    supplier, _ = ensure_pool_one(db)
    observed_at = synced_at or utcnow()
    records = []
    for item in items:
        record = db.scalar(select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.external_product_ref == item.external_ref,
        ))
        if not record:
            record = SupplierProduct(supplier_id=supplier.id, external_product_ref=item.external_ref)
            db.add(record)
        record.name_snapshot = item.name
        record.suffix = item.suffix
        record.resource_category = PUBLISHABLE_PRODUCT_CATEGORIES.get(item.external_ref)
        record.currency = "CNY"
        record.is_available = True
        record.source_endpoint = PRODUCTS_ENDPOINT
        record.last_synced_at = observed_at
        records.append(record)
    db.flush()
    return records


def get_supplier_product(db: Session, product_type_id: int) -> SupplierProduct:
    supplier, _ = ensure_pool_one(db)
    product = db.scalar(select(SupplierProduct).where(
        SupplierProduct.supplier_id == supplier.id,
        SupplierProduct.external_product_ref == str(product_type_id),
    ))
    if not product:
        raise ValueError("请先同步供应商商品")
    return product


def sync_regions(
    db: Session,
    product_type_id: int,
    items: list[SupplierRegionItem],
    source_endpoint: str,
    synced_at: datetime | None = None,
) -> list[SupplierRegion]:
    product = get_supplier_product(db, product_type_id)
    observed_at = synced_at or utcnow()
    records = []
    for item in items:
        # 必须先解析本地地区：_ensure_local_region 内部查库会触发 autoflush，
        # 若此时 SupplierRegion 已 add 但必填字段（source_endpoint 等）还没赋完，
        # SQLAlchemy 会把半成品提前 INSERT，导致 NOT NULL constraint failed。
        local_region = _ensure_local_region(db, item.country_code, item.country_name)
        record = db.scalar(select(SupplierRegion).where(
            SupplierRegion.supplier_product_id == product.id,
            SupplierRegion.external_country_ref == item.external_country_ref,
            SupplierRegion.external_city_ref == item.external_city_ref,
        ))
        if not record:
            record = SupplierRegion(
                supplier_product_id=product.id,
                external_country_ref=item.external_country_ref,
                external_city_ref=item.external_city_ref,
            )
            db.add(record)
        record.country_code = item.country_code.strip().upper()
        record.country_name = item.country_name
        record.city_name = item.city_name
        record.local_region_id = local_region.id
        record.is_available = True
        record.source_endpoint = source_endpoint
        record.last_synced_at = observed_at
        records.append(record)
    db.flush()
    return records


def _ensure_local_region(db: Session, country_code: str, country_name: str) -> Region:
    code = country_code.strip().upper()
    with db.no_autoflush:
        region = db.scalar(select(Region).where(Region.country_code == code).order_by(Region.is_active.desc(), Region.id).limit(1))
        if region:
            return region
        name = country_name.strip() or code
        duplicate = db.scalar(select(Region).where(Region.name == name))
    if duplicate and duplicate.country_code != code:
        name = f"{name} ({code})"
    region = Region(name=name[:80], country_code=code, is_active=True)
    db.add(region)
    db.flush()
    return region


def sync_supplier_catalog(db: Session) -> dict[str, int]:
    """按已有供应商国家和零售价规则幂等生成静态住宅官网目录。"""
    supplier, pool = ensure_pool_one(db)
    supplier_products = db.scalars(select(SupplierProduct).where(
        SupplierProduct.supplier_id == supplier.id,
        SupplierProduct.external_product_ref.in_(PUBLISHABLE_PRODUCT_CATEGORIES),
        SupplierProduct.is_available.is_(True),
    )).all()
    prices = db.scalars(select(RetailPriceRule).where(
        RetailPriceRule.resource_category.in_(PUBLISHABLE_PRODUCT_CATEGORIES.values()),
    )).all()
    price_by_key = {
        (item.resource_category, item.country_code.upper(), item.billing_days): item
        for item in prices
    }
    created = mapped_regions = skipped_no_price = 0

    for supplier_product in supplier_products:
        category = PUBLISHABLE_PRODUCT_CATEGORIES[supplier_product.external_product_ref]
        supplier_product.resource_category = category
        regions = db.scalars(select(SupplierRegion).where(
            SupplierRegion.supplier_product_id == supplier_product.id,
            SupplierRegion.city_name.is_(None),
            SupplierRegion.is_available.is_(True),
        )).all()
        for supplier_region in regions:
            if supplier_region.local_region_id is None:
                supplier_region.local_region_id = _ensure_local_region(
                    db, supplier_region.country_code, supplier_region.country_name,
                ).id
                mapped_regions += 1
            local_region = db.get(Region, supplier_region.local_region_id)
            if not local_region:
                continue
            matching_prices = [
                price for (price_category, country_code, _), price in price_by_key.items()
                if price_category == category and country_code == supplier_region.country_code.upper()
            ]
            if not matching_prices:
                skipped_no_price += 1
                continue
            for retail_price in matching_prices:
                rule = db.scalar(select(ProductSupplyRule).where(
                    ProductSupplyRule.pool_id == pool.id,
                    ProductSupplyRule.supplier_product_id == supplier_product.id,
                    ProductSupplyRule.supplier_region_id == supplier_region.id,
                ).join(Product, Product.id == ProductSupplyRule.product_id).where(
                    Product.region_id == local_region.id,
                    Product.billing_days == retail_price.billing_days,
                ))
                if rule:
                    continue
                product = Product(
                    # 商品名用客户端短名（"静态住宅IP"），分类下拉用技术名（"静态住宅原生IP"）。
                    # 两者分开：客户看的是买什么，运营看的是这条线到底是什么资源。
                    name=f"{local_region.name} {CATEGORY_LABELS.get(category, RESOURCE_CATEGORY_CODES[category])} {retail_price.billing_days}天",
                    product_type=CATEGORY_LABELS.get(category, RESOURCE_CATEGORY_CODES[category]),
                    price=float(retail_price.unit_price),
                    billing_days=retail_price.billing_days,
                    region_id=local_region.id,
                    is_active=True,
                    is_in_stock=False,
                )
                db.add(product)
                db.flush()
                db.add(ProductSupplyRule(
                    product_id=product.id,
                    region_id=local_region.id,
                    pool_id=pool.id,
                    supplier_product_id=supplier_product.id,
                    supplier_region_id=supplier_region.id,
                    mode="manual",
                    is_enabled=False,
                ))
                created += 1
    db.flush()
    return {"created": created, "mapped_regions": mapped_regions, "skipped_no_price": skipped_no_price}


def save_inventory(
    db: Session,
    product_type_id: int,
    items: list[InventoryItem],
    source_endpoint: str = INVENTORY_ENDPOINT,
    checked_at: datetime | None = None,
) -> list[SupplierInventorySnapshot]:
    supplier, _ = ensure_pool_one(db)
    product = get_supplier_product(db, product_type_id)
    observed_at = checked_at or utcnow()
    records = []
    for item in items:
        region = db.scalar(select(SupplierRegion).where(
            SupplierRegion.supplier_product_id == product.id,
            SupplierRegion.country_code == item.country_code,
            SupplierRegion.city_name == item.city_name,
        ))
        record = SupplierInventorySnapshot(
            supplier_id=supplier.id,
            supplier_product_id=product.id,
            supplier_region_id=region.id if region else None,
            country_code=item.country_code,
            city_name=item.city_name,
            availability=item.availability,
            available_quantity=None,
            source_endpoint=source_endpoint,
            checked_at=observed_at,
        )
        db.add(record)
        records.append(record)
    db.flush()
    return records


def save_quotes(db: Session, items: list[QuoteItem], quoted_at: datetime | None = None) -> list[SupplierQuote]:
    supplier, _ = ensure_pool_one(db)
    observed_at = quoted_at or utcnow()
    products = {
        product.external_product_ref: product
        for product in db.scalars(select(SupplierProduct).where(SupplierProduct.supplier_id == supplier.id)).all()
    }
    records = []
    for item in items:
        product = products.get(item.external_product_ref)
        record = SupplierQuote(
            supplier_id=supplier.id,
            supplier_product_id=product.id if product else None,
            external_product_ref=item.external_product_ref,
            country_code=item.country_code,
            duration_days=item.duration_days,
            unit_cost=Decimal(item.unit_cost),
            total_cost=Decimal(item.total_cost) if item.total_cost is not None else None,
            currency=item.currency,
            is_custom=item.is_custom,
            is_available=True,
            source_endpoint=QUOTES_ENDPOINT,
            quoted_at=observed_at,
        )
        db.add(record)
        records.append(record)
    db.flush()
    return records


def map_supplier_product(
    db: Session,
    supplier_product_id: int,
    local_product_id: int | None,
    resource_category: str | None = None,
) -> SupplierProduct:
    record = db.get(SupplierProduct, supplier_product_id)
    if not record:
        raise ValueError("供应商商品不存在")
    if local_product_id is not None and not db.get(Product, local_product_id):
        raise ValueError("玖玖云商品不存在")
    if resource_category is not None and resource_category not in RESOURCE_CATEGORY_CODES:
        raise ValueError("资源分类无效")
    record.local_product_id = local_product_id
    record.resource_category = resource_category
    db.flush()
    return record


def save_ip_segment_snapshots(
    db: Session,
    product_type_id: int,
    country_code: str,
    quantity: int,
    duration_code: int | None,
    segments: tuple[tuple[str, bool], ...],
    request_sufficient: bool,
    sync_run_id: int,
) -> list[SupplierIpSegmentSnapshot]:
    supplier, _ = ensure_pool_one(db)
    product = get_supplier_product(db, product_type_id)
    region = db.scalar(select(SupplierRegion).where(
        SupplierRegion.supplier_product_id == product.id,
        SupplierRegion.country_code == country_code.upper(),
        SupplierRegion.city_name.is_(None),
    ))
    checked_at = utcnow()
    canonical_segments: dict[str, bool] = {}
    for cidr, is_sufficient in segments:
        try:
            canonical = str(ip_network(cidr, strict=False))
        except ValueError as exc:
            raise SupplierInvalidResponseError("供应商网段格式无效") from exc
        canonical_segments[canonical] = is_sufficient
    records = [SupplierIpSegmentSnapshot(
        supplier_id=supplier.id,
        supplier_product_id=product.id,
        supplier_region_id=region.id if region else None,
        sync_run_id=sync_run_id,
        country_code=country_code.upper(),
        city_name=None,
        cidr=canonical,
        requested_quantity=quantity,
        duration_code=duration_code,
        is_segment_sufficient=is_sufficient,
        is_request_sufficient=request_sufficient,
        checked_at=checked_at,
    ) for cidr, is_sufficient in canonical_segments.items()]
    db.add_all(records)
    db.flush()
    return records


def map_supplier_region(db: Session, supplier_region_id: int, local_region_id: int | None) -> SupplierRegion:
    record = db.get(SupplierRegion, supplier_region_id)
    if not record:
        raise ValueError("供应商地区不存在")
    if local_region_id is not None and not db.get(Region, local_region_id):
        raise ValueError("玖玖云地区不存在")
    record.local_region_id = local_region_id
    db.flush()
    return record


def configure_manual_supply_rule(
    db: Session,
    product_id: int,
    region_id: int,
    supplier_product_id: int,
    supplier_region_id: int,
    is_enabled: bool,
) -> ProductSupplyRule:
    _, pool = ensure_pool_one(db)
    product = db.get(Product, product_id)
    region = db.get(Region, region_id)
    supplier_product = db.get(SupplierProduct, supplier_product_id)
    supplier_region = db.get(SupplierRegion, supplier_region_id)
    if not product or not region:
        raise ValueError("玖玖云商品或地区不存在")
    if product.region_id != region.id:
        raise ValueError("玖玖云商品与地区不匹配")
    if not supplier_product or not supplier_region or supplier_region.supplier_product_id != supplier_product.id:
        raise ValueError("供应商商品与地区不匹配")
    rule = db.scalar(select(ProductSupplyRule).where(
        ProductSupplyRule.product_id == product.id,
        ProductSupplyRule.region_id == region.id,
        ProductSupplyRule.pool_id == pool.id,
    ))
    if not rule:
        rule = ProductSupplyRule(product_id=product.id, region_id=region.id, pool_id=pool.id)
        db.add(rule)
    rule.supplier_product_id = supplier_product.id
    rule.supplier_region_id = supplier_region.id
    rule.mode = "manual"
    rule.is_enabled = is_enabled
    db.flush()
    return rule
