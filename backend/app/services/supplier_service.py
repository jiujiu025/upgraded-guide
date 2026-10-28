from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Product, ProductSupplyRule, Region, ResourcePool, ResourcePoolSupplier, Supplier,
    SupplierInventorySnapshot, SupplierProduct, SupplierQuote, SupplierRegion, SupplierSyncRun, User,
)
from app.services.audit_service import write_audit
from app.suppliers.base import InventoryItem, QuoteItem, SupplierProductItem, SupplierRegionItem

SUPPLIER_CODE = "quanqiudaili"
POOL_CODE = "global_proxy_pool"
PRODUCTS_ENDPOINT = "/externalapi/conpons/getProductList"
COUNTRIES_ENDPOINT = "/externalapi/inventory/countryList"
CITIES_ENDPOINT = "/externalapi/inventory/cityList"
INVENTORY_ENDPOINT = "/externalapi/inventory/getBuyProductSku"
INVENTORY_CHECK_ENDPOINT = "/externalapi/product_order/checkInventory"
QUOTES_ENDPOINT = "/externalapi/product_price/getProductUnitPrice"


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


def sync_products(db: Session, items: list[SupplierProductItem]) -> list[SupplierProduct]:
    supplier, _ = ensure_pool_one(db)
    synced_at = utcnow()
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
        record.currency = "CNY"
        record.is_available = True
        record.source_endpoint = PRODUCTS_ENDPOINT
        record.last_synced_at = synced_at
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
) -> list[SupplierRegion]:
    product = get_supplier_product(db, product_type_id)
    synced_at = utcnow()
    records = []
    for item in items:
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
        record.country_code = item.country_code
        record.country_name = item.country_name
        record.city_name = item.city_name
        record.is_available = True
        record.source_endpoint = source_endpoint
        record.last_synced_at = synced_at
        records.append(record)
    db.flush()
    return records


def save_inventory(
    db: Session,
    product_type_id: int,
    items: list[InventoryItem],
    source_endpoint: str = INVENTORY_ENDPOINT,
) -> list[SupplierInventorySnapshot]:
    supplier, _ = ensure_pool_one(db)
    product = get_supplier_product(db, product_type_id)
    checked_at = utcnow()
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
            checked_at=checked_at,
        )
        db.add(record)
        records.append(record)
    db.flush()
    return records


def save_quotes(db: Session, items: list[QuoteItem]) -> list[SupplierQuote]:
    supplier, _ = ensure_pool_one(db)
    quoted_at = utcnow()
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
            quoted_at=quoted_at,
        )
        db.add(record)
        records.append(record)
    db.flush()
    return records


def map_supplier_product(db: Session, supplier_product_id: int, local_product_id: int | None) -> SupplierProduct:
    record = db.get(SupplierProduct, supplier_product_id)
    if not record:
        raise ValueError("供应商商品不存在")
    if local_product_id is not None and not db.get(Product, local_product_id):
        raise ValueError("玖玖云商品不存在")
    record.local_product_id = local_product_id
    db.flush()
    return record


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
    if supplier_product.local_product_id != product.id or supplier_region.local_region_id != region.id:
        raise ValueError("请先完成供应商商品和地区映射")
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
