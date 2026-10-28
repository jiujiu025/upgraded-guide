import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    CatalogPublication, Product, Region, Supplier, SupplierInventorySnapshot, SupplierProduct,
    SupplierQuote, SupplierRegion, SupplierSyncRun, User,
)
from app.services.audit_service import write_audit
from app.services.supplier_service import CITIES_ENDPOINT, INVENTORY_CHECK_ENDPOINT, utcnow


class PublicationError(ValueError):
    pass


def _latest_run(db: Session, supplier_id: int, sync_type: str) -> SupplierSyncRun | None:
    return db.scalar(select(SupplierSyncRun).where(
        SupplierSyncRun.supplier_id == supplier_id, SupplierSyncRun.sync_type == sync_type,
    ).order_by(SupplierSyncRun.started_at.desc(), SupplierSyncRun.id.desc()).limit(1))


def _require_fresh(db: Session, supplier_id: int, sync_type: str, observed_at, ttl: int) -> None:
    run = _latest_run(db, supplier_id, sync_type)
    if run and run.status != "success":
        raise PublicationError(f"{sync_type} 最近同步未成功，禁止新增发布")
    if not observed_at or observed_at < utcnow() - timedelta(minutes=ttl):
        raise PublicationError(f"{sync_type} 快照已过期，禁止新增发布")


def _catalog_values(product: Product) -> dict:
    return {
        "product_id": product.id,
        "region_id": product.region_id,
        "price": product.price,
        "is_in_stock": product.is_in_stock,
        "is_active": product.is_active,
    }


def _latest_publication(db: Session, product_id: int) -> CatalogPublication | None:
    return db.scalar(select(CatalogPublication).where(
        CatalogPublication.product_id == product_id,
    ).order_by(CatalogPublication.version.desc()).limit(1))


def publish_catalog(db: Session, actor: User, product_id: int, region_id: int,
                    inventory_id: int, quote_id: int, price: float) -> CatalogPublication:
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    region = db.get(Region, region_id)
    inventory = db.get(SupplierInventorySnapshot, inventory_id)
    quote = db.get(SupplierQuote, quote_id)
    if not product or not region or not product.is_active or not region.is_active or product.region_id != region_id:
        raise PublicationError("玖玖云商品或地区映射无效")
    if not inventory or not quote:
        raise PublicationError("库存或报价快照不存在")
    supplier_product = db.get(SupplierProduct, inventory.supplier_product_id)
    supplier_region = db.get(SupplierRegion, inventory.supplier_region_id) if inventory.supplier_region_id else None
    supplier = db.get(Supplier, inventory.supplier_id)
    if not supplier or supplier.status != "active" or not supplier_product or not supplier_region:
        raise PublicationError("供应商快照字段不完整或供应商不可用")
    if (supplier_product.supplier_id != supplier.id or supplier_product.local_product_id != product.id
            or supplier_region.supplier_product_id != supplier_product.id
            or supplier_region.local_region_id != region.id
            or inventory.country_code != supplier_region.country_code
            or inventory.city_name != supplier_region.city_name
            or region.country_code != supplier_region.country_code):
        raise PublicationError("商品、地区与库存快照映射不一致")
    if (quote.supplier_id != supplier.id or quote.supplier_product_id != supplier_product.id
            or quote.external_product_ref != supplier_product.external_product_ref
            or quote.country_code not in (None, region.country_code)
            or quote.duration_days not in (None, product.billing_days)):
        raise PublicationError("报价快照与商品、地区或周期不匹配")
    if (not supplier_product.name_snapshot or not supplier_product.external_product_ref
            or not supplier_region.country_name or not supplier_region.country_code
            or not quote.currency or quote.unit_cost is None or quote.unit_cost <= 0
            or not quote.is_available or not supplier_product.is_available
            or not supplier_region.is_available or inventory.availability != "available"):
        raise PublicationError("快照字段不完整、不可用或无库存")
    settings = get_settings()
    _require_fresh(db, supplier.id, "products", supplier_product.last_synced_at, settings.supplier_catalog_ttl_minutes)
    region_type = "cities" if supplier_region.source_endpoint == CITIES_ENDPOINT else "countries"
    _require_fresh(db, supplier.id, region_type, supplier_region.last_synced_at, settings.supplier_catalog_ttl_minutes)
    inventory_type = "inventory_check" if inventory.source_endpoint == INVENTORY_CHECK_ENDPOINT else "inventory"
    _require_fresh(db, supplier.id, inventory_type, inventory.checked_at, settings.supplier_inventory_ttl_minutes)
    _require_fresh(db, supplier.id, "quotes", quote.quoted_at, settings.supplier_quote_ttl_minutes)
    latest_inventory = db.scalar(select(SupplierInventorySnapshot.id).where(
        SupplierInventorySnapshot.supplier_product_id == supplier_product.id,
        SupplierInventorySnapshot.supplier_region_id == supplier_region.id,
    ).order_by(SupplierInventorySnapshot.checked_at.desc(), SupplierInventorySnapshot.id.desc()).limit(1))
    latest_quote = db.scalar(select(SupplierQuote.id).where(
        SupplierQuote.supplier_product_id == supplier_product.id,
        SupplierQuote.country_code == quote.country_code,
        SupplierQuote.duration_days == quote.duration_days,
    ).order_by(SupplierQuote.quoted_at.desc(), SupplierQuote.id.desc()).limit(1))
    if latest_inventory != inventory.id or latest_quote != quote.id:
        raise PublicationError("请选择当前最新的库存和报价快照")
    previous = _latest_publication(db, product.id)
    if (previous and previous.withdrawn_at is None
            and previous.inventory_snapshot_id == inventory.id and previous.quote_snapshot_id == quote.id
            and previous.after_values["price"] == price):
        raise PublicationError("同一快照及售价已发布，请勿重复提交")
    before = _catalog_values(product)
    product.price = price
    product.is_in_stock = True
    after = _catalog_values(product)
    snapshot_version = {
        "product_id": supplier_product.id, "product_at": supplier_product.last_synced_at.isoformat(),
        "region_id": supplier_region.id, "region_at": supplier_region.last_synced_at.isoformat(),
        "inventory_id": inventory.id, "inventory_at": inventory.checked_at.isoformat(),
        "quote_id": quote.id, "quote_at": quote.quoted_at.isoformat(),
    }
    publication = CatalogPublication(
        product_id=product.id, region_id=region.id, version=(previous.version + 1 if previous else 1),
        supplier_product_id=supplier_product.id, supplier_region_id=supplier_region.id,
        inventory_snapshot_id=inventory.id, quote_snapshot_id=quote.id, actor_id=actor.id,
        before_values=before, after_values=after, snapshot_version=snapshot_version,
    )
    db.add(publication)
    db.flush()
    write_audit(db, actor, "publish", "catalog_publication", publication.id, json.dumps({
        "version": publication.version, "snapshot_version": snapshot_version,
        "product_id": product.id, "region_id": region.id, "before": before, "after": after,
    }, ensure_ascii=False))
    return publication


def withdraw_catalog(db: Session, actor: User, publication_id: int) -> CatalogPublication:
    publication = db.get(CatalogPublication, publication_id)
    if not publication:
        raise PublicationError("发布版本不存在")
    product = db.scalar(select(Product).where(Product.id == publication.product_id).with_for_update())
    if (publication.withdrawn_at or _latest_publication(db, product.id).id != publication.id
            or _catalog_values(product) != publication.after_values):
        raise PublicationError("仅能撤回当前版本，且目录不能已有其他修改")
    before = _catalog_values(product)
    product.price = publication.before_values["price"]
    product.is_in_stock = publication.before_values["is_in_stock"]
    publication.withdrawn_at = utcnow()
    publication.withdrawn_by = actor.id
    after = _catalog_values(product)
    write_audit(db, actor, "withdraw", "catalog_publication", publication.id, json.dumps({
        "version": publication.version, "snapshot_version": publication.snapshot_version,
        "product_id": product.id, "region_id": publication.region_id, "before": before, "after": after,
    }, ensure_ascii=False))
    return publication
