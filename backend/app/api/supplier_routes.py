from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    CatalogPublication, ProductSupplyRule, ResourcePool, Supplier, SupplierInventorySnapshot, SupplierProduct, SupplierQuote,
    SupplierRegion, SupplierSyncRun, User,
)
from app.services.audit_service import write_audit
from app.services.catalog_publication_service import PublicationError, publish_catalog, withdraw_catalog
from app.services.supplier_service import (
    CITIES_ENDPOINT, COUNTRIES_ENDPOINT, INVENTORY_CHECK_ENDPOINT, INVENTORY_ENDPOINT, begin_sync_run,
    configure_manual_supply_rule, ensure_pool_one, finish_sync_run, map_supplier_product,
    map_supplier_region, save_inventory, save_quotes, sync_products, sync_regions,
)
from app.suppliers.base import (
    InventoryItem, ProcurementDisabledError, SupplierBusinessError, SupplierCapabilityUnsupportedError,
    SupplierConfigurationError, SupplierError, SupplierInvalidResponseError, SupplierRateLimitError,
    SupplierTimeoutError, reject_procurement,
)
from app.suppliers.global_proxy import GlobalProxyReadOnlyAdapter

router = APIRouter(prefix="/admin/suppliers", tags=["供应商只读接入"])


class ProductMapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_product_id: int | None = None


class RegionMapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_region_id: int | None = None


class SupplyRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    region_id: int
    supplier_product_id: int
    supplier_region_id: int
    is_enabled: bool = False


class QuoteSyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_type_id: int | None = None
    bill_timelen: int | None = Field(default=None, ge=1, le=3)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    bandwidth_num: int | None = Field(default=None, ge=10, le=1000)

    @model_validator(mode="after")
    def validate_quote_request(self):
        if self.product_type_id not in (None, 11):
            raise ValueError("仅支持完整单价表或产品 11 首购试算")
        if self.product_type_id == 11:
            if self.bill_timelen is None or self.country is None or self.bandwidth_num is None:
                raise ValueError("产品 11 试算必须提供周期、国家和带宽")
            if self.country.upper() not in {"US", "DE", "SG"} or self.bandwidth_num % 10:
                raise ValueError("产品 11 国家或带宽不符合官方文档范围")
        elif any(value is not None for value in (self.bill_timelen, self.country, self.bandwidth_num)):
            raise ValueError("完整单价表查询不接受产品 11 参数")
        return self


class InventoryCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_type_id: int = 2
    quantity: int = Field(ge=1, le=300)
    country_code: str = Field(min_length=2, max_length=2)
    duration_code: int | None = Field(default=None, ge=0, le=4)


class CatalogPublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    region_id: int = Field(gt=0)
    inventory_snapshot_id: int = Field(gt=0)
    quote_snapshot_id: int = Field(gt=0)
    price: float = Field(gt=0, allow_inf_nan=False)


def get_global_proxy_adapter() -> Generator[GlobalProxyReadOnlyAdapter, None, None]:
    settings = get_settings()
    key = settings.global_proxy_api_key.get_secret_value() if settings.global_proxy_api_key else None
    adapter = GlobalProxyReadOnlyAdapter(
        api_key=key,
        base_url=settings.global_proxy_base_url,
        timeout_seconds=settings.supplier_http_timeout_seconds,
        max_attempts=settings.supplier_read_retry_attempts,
        backoff_base_seconds=settings.supplier_retry_backoff_seconds,
        max_retry_delay_seconds=settings.supplier_retry_max_delay_seconds,
    )
    try:
        yield adapter
    finally:
        adapter.close()


def supplier_error(exc: SupplierError) -> HTTPException:
    if isinstance(exc, SupplierConfigurationError):
        status = 503
    elif isinstance(exc, SupplierTimeoutError):
        status = 504
    elif isinstance(exc, SupplierRateLimitError):
        status = 503
    elif isinstance(exc, SupplierCapabilityUnsupportedError):
        status = 501
    elif isinstance(exc, (SupplierInvalidResponseError, SupplierBusinessError)):
        status = 502
    else:
        status = 503
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def serialize_model(record) -> dict:
    return {column.name: getattr(record, column.name) for column in record.__table__.columns}


def api_key_configured() -> bool:
    key = get_settings().global_proxy_api_key
    return bool(key and key.get_secret_value())


def fail_sync_run(db: Session, user: User, run_id: int, error_code: str, message: str) -> None:
    db.rollback()
    finish_sync_run(db, user, run_id, "failed", error_code=error_code, error_message=message)


def latest_runs(db: Session, sync_types: set[str]) -> list[SupplierSyncRun]:
    records = db.scalars(
        select(SupplierSyncRun)
        .where(SupplierSyncRun.sync_type.in_(sync_types))
        .order_by(SupplierSyncRun.started_at.desc(), SupplierSyncRun.id.desc())
    ).all()
    latest_by_type = {}
    for record in records:
        latest_by_type.setdefault(record.sync_type, record)
    return list(latest_by_type.values())


def snapshot_state(
    db: Session,
    snapshot_at: datetime | None,
    sync_types: set[str],
    ttl_minutes: int,
) -> str:
    runs = latest_runs(db, sync_types)
    if any(run.status == "running" for run in runs):
        return "syncing"
    if any(run.status in {"failed", "partial_failed"} for run in runs):
        return "query_failed"
    if not snapshot_at:
        return "not_synced"
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=ttl_minutes)
    return "stale" if snapshot_at < cutoff else "fresh"


def aggregate_states(states: list[str]) -> str:
    priority = {"fresh": 0, "out_of_stock": 1, "not_synced": 2, "stale": 3, "query_failed": 4, "syncing": 5}
    return max(states, key=lambda state: priority[state])


@router.get("")
def list_suppliers(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    ensure_pool_one(db)
    db.commit()
    settings = get_settings()
    suppliers = db.scalars(select(Supplier).order_by(Supplier.id)).all()
    return [{
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "status": item.status,
        "base_url": item.base_url,
        "api_key_configured": api_key_configured(),
        "auto_purchase_enabled": False,
    } for item in suppliers]


@router.get("/pool-1")
def get_pool_one(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    _, pool = ensure_pool_one(db)
    db.commit()
    return serialize_model(pool)


@router.get("/overview")
def supplier_overview(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    supplier, pool = ensure_pool_one(db)
    db.commit()
    settings = get_settings()
    product_synced_at = db.scalar(select(func.max(SupplierProduct.last_synced_at)))
    country_synced_at = db.scalar(select(func.max(SupplierRegion.last_synced_at)).where(SupplierRegion.city_name.is_(None)))
    city_synced_at = db.scalar(select(func.max(SupplierRegion.last_synced_at)).where(SupplierRegion.city_name.is_not(None)))
    inventory_synced_at = db.scalar(select(func.max(SupplierInventorySnapshot.checked_at)))
    quote_synced_at = db.scalar(select(func.max(SupplierQuote.quoted_at)))
    recent_run = db.scalar(select(SupplierSyncRun).order_by(SupplierSyncRun.started_at.desc()).limit(1))
    recent_failure = recent_run if recent_run and recent_run.status in {"failed", "partial_failed"} else None
    return {
        "supplier": {
            "id": supplier.id,
            "code": supplier.code,
            "name": supplier.name,
            "status": supplier.status,
            "base_url": supplier.base_url,
            "api_key_configured": api_key_configured(),
            "auto_purchase_enabled": False,
            "last_sync_at": recent_run.finished_at if recent_run else None,
            "last_sync_status": recent_run.status if recent_run else "not_synced",
            "last_error_code": recent_failure.error_code if recent_failure else None,
            "last_error_message": recent_failure.error_message if recent_failure else None,
        },
        "pool": {
            "id": pool.id,
            "code": pool.code,
            "name": pool.name,
            "status": pool.status,
            "strategy": pool.strategy,
        },
        "states": {
            "products": snapshot_state(db, product_synced_at, {"products"}, settings.supplier_catalog_ttl_minutes),
            "regions": aggregate_states([
                snapshot_state(db, country_synced_at, {"countries"}, settings.supplier_catalog_ttl_minutes),
                snapshot_state(db, city_synced_at, {"cities"}, settings.supplier_catalog_ttl_minutes),
            ]),
            "inventory": snapshot_state(db, inventory_synced_at, {"inventory", "inventory_check"}, settings.supplier_inventory_ttl_minutes),
            "quotes": snapshot_state(db, quote_synced_at, {"quotes"}, settings.supplier_quote_ttl_minutes),
        },
        "counts": {
            "products": db.scalar(select(func.count()).select_from(SupplierProduct)) or 0,
            "regions": db.scalar(select(func.count()).select_from(SupplierRegion)) or 0,
            "inventory_snapshots": db.scalar(select(func.count()).select_from(SupplierInventorySnapshot)) or 0,
            "quote_snapshots": db.scalar(select(func.count()).select_from(SupplierQuote)) or 0,
            "enabled_manual_rules": db.scalar(select(func.count()).select_from(ProductSupplyRule).where(ProductSupplyRule.is_enabled.is_(True))) or 0,
        },
    }


@router.get("/sync-runs")
def list_sync_runs(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(SupplierSyncRun).order_by(SupplierSyncRun.started_at.desc()).limit(limit)
    return [serialize_model(item) for item in db.scalars(query).all()]


@router.get("/products")
def list_supplier_products(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    settings = get_settings()
    records = db.scalars(select(SupplierProduct).order_by(SupplierProduct.id)).all()
    return [{**serialize_model(item), "data_state": snapshot_state(db, item.last_synced_at, {"products"}, settings.supplier_catalog_ttl_minutes)} for item in records]


@router.post("/sync/products")
def sync_supplier_products(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    run = begin_sync_run(db, user, "products")
    try:
        records = sync_products(db, adapter.list_products())
        write_audit(db, user, "sync", "supplier_products", detail=f"供应商=quanqiudaili; 数量={len(records)}")
        db.commit()
        completed = finish_sync_run(db, user, run.id, "success", len(records), len(records))
        return {"count": len(records), "run_id": completed.id, "status": completed.status, "source_endpoint": "/externalapi/conpons/getProductList"}
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.post("/sync/countries")
def sync_supplier_countries(
    product_type_id: int = Query(default=2),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    run = begin_sync_run(db, user, "countries")
    try:
        records = sync_regions(db, product_type_id, adapter.list_countries(product_type_id), COUNTRIES_ENDPOINT)
        write_audit(db, user, "sync", "supplier_regions", detail=f"类型=国家; 产品={product_type_id}; 数量={len(records)}")
        db.commit()
        completed = finish_sync_run(db, user, run.id, "success", len(records), len(records))
        return {"count": len(records), "run_id": completed.id, "status": completed.status, "source_endpoint": COUNTRIES_ENDPOINT}
    except ValueError as exc:
        fail_sync_run(db, user, run.id, "LOCAL_MAPPING_REQUIRED", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.post("/sync/cities")
def sync_supplier_cities(
    product_type_id: int = Query(default=2),
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    country_id: str | None = Query(default=None, pattern=r"^[0-9]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    run = begin_sync_run(db, user, "cities")
    try:
        records = sync_regions(db, product_type_id, adapter.list_cities(product_type_id, country_code, country_id), CITIES_ENDPOINT)
        write_audit(db, user, "sync", "supplier_regions", detail=f"类型=城市; 产品={product_type_id}; 国家ID={country_id or '全部'}; 数量={len(records)}")
        db.commit()
        completed = finish_sync_run(db, user, run.id, "success", len(records), len(records))
        return {"count": len(records), "run_id": completed.id, "status": completed.status, "source_endpoint": CITIES_ENDPOINT}
    except ValueError as exc:
        fail_sync_run(db, user, run.id, "LOCAL_MAPPING_REQUIRED", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.get("/regions")
def list_supplier_regions(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    settings = get_settings()
    records = db.scalars(select(SupplierRegion).order_by(SupplierRegion.id)).all()
    return [{
        **serialize_model(item),
        "data_state": snapshot_state(
            db,
            item.last_synced_at,
            {"cities" if item.source_endpoint == CITIES_ENDPOINT else "countries"},
            settings.supplier_catalog_ttl_minutes,
        ),
    } for item in records]


@router.post("/sync/inventory")
def sync_supplier_inventory(
    country_code: str = Query(min_length=2, max_length=2),
    product_type_id: int = Query(default=2),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    run = begin_sync_run(db, user, "inventory")
    try:
        records = save_inventory(db, product_type_id, adapter.get_inventory(product_type_id, country_code))
        write_audit(db, user, "sync", "supplier_inventory", detail=f"产品={product_type_id}; 国家={country_code.upper()}; 数量={len(records)}")
        db.commit()
        completed = finish_sync_run(db, user, run.id, "success", len(records), len(records))
        return {"count": len(records), "run_id": completed.id, "status": completed.status, "items": [serialize_model(item) for item in records]}
    except ValueError as exc:
        fail_sync_run(db, user, run.id, "LOCAL_MAPPING_REQUIRED", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.get("/inventory")
def list_inventory(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    settings = get_settings()
    query = select(SupplierInventorySnapshot).order_by(SupplierInventorySnapshot.checked_at.desc()).limit(500)
    records = db.scalars(query).all()
    result = []
    for item in records:
        sync_type = "inventory_check" if item.source_endpoint == INVENTORY_CHECK_ENDPOINT else "inventory"
        state = snapshot_state(db, item.checked_at, {sync_type}, settings.supplier_inventory_ttl_minutes)
        if state == "fresh" and item.availability == "unavailable":
            state = "out_of_stock"
        result.append({
            **serialize_model(item),
            "data_state": state,
            "is_realtime_available": state == "fresh" and item.availability == "available",
        })
    return result


@router.post("/check-inventory")
def check_supplier_inventory(
    payload: InventoryCheckIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    run = begin_sync_run(db, user, "inventory_check")
    try:
        result = adapter.check_inventory(
            payload.product_type_id,
            payload.quantity,
            payload.country_code,
            payload.duration_code,
        )
        records = save_inventory(
            db,
            payload.product_type_id,
            [InventoryItem(
                country_code=payload.country_code.upper(),
                city_name=None,
                availability="available" if result.is_sufficient else "unavailable",
            )],
            source_endpoint=INVENTORY_CHECK_ENDPOINT,
        )
        write_audit(
            db,
            user,
            "sync",
            "supplier_inventory_check",
            detail=f"产品={payload.product_type_id}; 国家={payload.country_code.upper()}; 充足={result.is_sufficient}",
        )
        db.commit()
        completed = finish_sync_run(db, user, run.id, "success", 1, len(records))
        return {
            "is_sufficient": result.is_sufficient,
            "segments": [{"cidr": cidr, "is_sufficient": sufficient} for cidr, sufficient in result.segments],
            "run_id": completed.id,
            "status": completed.status,
            "source_endpoint": INVENTORY_CHECK_ENDPOINT,
        }
    except ValueError as exc:
        fail_sync_run(db, user, run.id, "LOCAL_MAPPING_REQUIRED", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.post("/sync/quotes")
def sync_supplier_quotes(
    payload: QuoteSyncIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    run = begin_sync_run(db, user, "quotes")
    try:
        parameters = payload.model_dump(exclude_none=True)
        if "country" in parameters:
            parameters["country"] = parameters["country"].upper()
        records = save_quotes(db, adapter.get_quotes(**parameters))
        write_audit(db, user, "sync", "supplier_quotes", detail=f"数量={len(records)}")
        db.commit()
        completed = finish_sync_run(db, user, run.id, "success", len(records), len(records))
        return {"count": len(records), "run_id": completed.id, "status": completed.status, "source_endpoint": "/externalapi/product_price/getProductUnitPrice"}
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.get("/quotes")
def list_quotes(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    settings = get_settings()
    query = select(SupplierQuote).order_by(SupplierQuote.quoted_at.desc()).limit(500)
    records = db.scalars(query).all()
    return [{
        **serialize_model(item),
        "data_state": snapshot_state(db, item.quoted_at, {"quotes"}, settings.supplier_quote_ttl_minutes),
    } for item in records]


@router.patch("/products/{supplier_product_id}/mapping")
def update_product_mapping(
    supplier_product_id: int,
    payload: ProductMapIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    try:
        record = map_supplier_product(db, supplier_product_id, payload.local_product_id)
        write_audit(db, user, "update", "supplier_product_mapping", record.id, f"玖玖云商品={payload.local_product_id}")
        db.commit()
        return serialize_model(record)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/regions/{supplier_region_id}/mapping")
def update_region_mapping(
    supplier_region_id: int,
    payload: RegionMapIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    try:
        record = map_supplier_region(db, supplier_region_id, payload.local_region_id)
        write_audit(db, user, "update", "supplier_region_mapping", record.id, f"玖玖云地区={payload.local_region_id}")
        db.commit()
        return serialize_model(record)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/supply-rules")
def upsert_supply_rule(
    payload: SupplyRuleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    try:
        rule = configure_manual_supply_rule(db, **payload.model_dump())
        write_audit(db, user, "update", "product_supply_rule", rule.id, f"模式=manual; 启用={rule.is_enabled}")
        db.commit()
        return serialize_model(rule)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/supply-rules")
def list_supply_rules(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    return [serialize_model(item) for item in db.scalars(select(ProductSupplyRule).order_by(ProductSupplyRule.id)).all()]


@router.get("/publications")
def list_publications(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    records = db.scalars(select(CatalogPublication).order_by(CatalogPublication.id.desc()).limit(100)).all()
    return [serialize_model(item) for item in records]


@router.post("/publications")
def publish_supplier_snapshot(
    payload: CatalogPublishIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    try:
        record = publish_catalog(db, user, payload.product_id, payload.region_id,
                                 payload.inventory_snapshot_id, payload.quote_snapshot_id, payload.price)
        db.commit()
        return serialize_model(record)
    except PublicationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="发布版本已变化，请刷新后重试") from exc


@router.post("/publications/{publication_id}/withdraw")
def withdraw_supplier_publication(
    publication_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    try:
        record = withdraw_catalog(db, user, publication_id)
        db.commit()
        return serialize_model(record)
    except PublicationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/procure", status_code=403)
@router.post("/renew", status_code=403)
@router.post("/cancel", status_code=403)
@router.post("/refund", status_code=403)
def procure_is_disabled(_: User = Depends(require_roles("super_admin", "admin"))):
    try:
        reject_procurement()
    except ProcurementDisabledError as exc:
        raise HTTPException(status_code=403, detail={"code": exc.code, "message": str(exc)}) from exc
