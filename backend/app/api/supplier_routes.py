from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    CatalogPublication, ProductSupplyRule, ResourcePool, ResourcePoolSupplier, Supplier,
    SupplierInventorySnapshot, SupplierIpSegmentSnapshot, SupplierProduct, SupplierQuote, SupplierRegion,
    SupplierSyncRun, User,
)
from app.services.audit_service import write_audit
from app.services.catalog_publication_service import PublicationError, publish_catalog, withdraw_catalog
from app.services.city_catalog_sync import sync_city_segments, sync_city_segments_985
from app.services.supplier_credentials import (
    SupplierCredentialError, clear_api_key, credential_encryption_ready, credential_source,
    get_supplier, has_configured_api_key, save_api_key, supplier_key_status,
)
from app.services.settings_service import AUTO_PROCUREMENT_KEY, get_flag, set_flag
from app.services.supplier_service import (
    CITIES_ENDPOINT, COUNTRIES_ENDPOINT, INVENTORY_CHECK_ENDPOINT, begin_sync_run,
    configure_manual_supply_rule, ensure_pool_one, finish_sync_run, map_supplier_product,
    map_supplier_region, RESOURCE_CATEGORY_CODES, save_inventory, save_ip_segment_snapshots, save_quotes,
    sync_products, sync_regions, sync_supplier_catalog,
)
from app.suppliers.base import (
    InventoryItem, ProcurementDisabledError, SupplierBusinessError, SupplierCapabilityUnsupportedError,
    SupplierConfigurationError, SupplierError, SupplierInvalidResponseError, SupplierRateLimitError,
    SupplierTimeoutError, reject_procurement,
)
from app.suppliers.factory import build_supplier_adapter
from app.suppliers.global_proxy import NATIVE_PRODUCT_TYPE_ID, GlobalProxyReadOnlyAdapter
from app.suppliers.factory import build_readonly_adapter

router = APIRouter(prefix="/admin/suppliers", tags=["供应商只读接入"])


class ProductMapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_product_id: int | None = None
    resource_category: str | None = None


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


def get_global_proxy_adapter(db: Session = Depends(get_db)) -> Generator[GlobalProxyReadOnlyAdapter, None, None]:
    try:
        adapter = build_readonly_adapter(db)
    except SupplierCredentialError as exc:
        raise HTTPException(status_code=503, detail={"code": "SUPPLIER_CONFIGURATION_ERROR", "message": str(exc)}) from exc
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


def api_key_configured(db: Session) -> bool:
    return has_configured_api_key(db)


async def _read_credential_body(request: Request) -> str:
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 8192:
        raise HTTPException(status_code=413, detail={"code": "SUPPLIER_CREDENTIAL_INVALID", "message": "凭据请求格式无效"})
    try:
        raw_body = await request.body()
        if len(raw_body) > 8192:
            raise ValueError
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail={"code": "SUPPLIER_CREDENTIAL_INVALID", "message": "凭据请求格式无效"}) from None
    if not isinstance(payload, dict) or set(payload) != {"api_key"} or not isinstance(payload["api_key"], str):
        raise HTTPException(status_code=422, detail={"code": "SUPPLIER_CREDENTIAL_INVALID", "message": "凭据请求格式无效"})
    value = payload["api_key"].strip()
    if not value or len(value) > 4096:
        raise HTTPException(status_code=422, detail={"code": "SUPPLIER_CREDENTIAL_INVALID", "message": "凭据长度无效"})
    return value


def _save_credential(db: Session, user: User, value: str, supplier: Supplier | None) -> dict:
    try:
        save_api_key(db, user, value, supplier=supplier)
        db.commit()
    except SupplierCredentialError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "SUPPLIER_CREDENTIAL_NOT_READY", "message": str(exc)}) from exc
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail={"code": "SUPPLIER_CREDENTIAL_SAVE_FAILED", "message": "凭据保存失败，请重试"}) from None
    return {"configured": True, "source": "admin"}


@router.put("/credential")
async def update_supplier_credential(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    """兼容入口：默认供应商（资源池 1）的 Key。"""
    value = await _read_credential_body(request)
    return _save_credential(db, user, value, None)


@router.delete("/credential")
def delete_supplier_credential(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    try:
        clear_api_key(db, user)
        db.commit()
    except SupplierCredentialError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "SUPPLIER_CREDENTIAL_NOT_FOUND", "message": str(exc)}) from exc
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail={"code": "SUPPLIER_CREDENTIAL_CLEAR_FAILED", "message": "凭据清除失败，请重试"}) from None
    return {"configured": api_key_configured(db), "source": credential_source(db)}


def _credential_supplier(db: Session, supplier_id: int) -> Supplier:
    supplier = get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return supplier


@router.get("/{supplier_id}/credential")
def get_supplier_credential(
    supplier_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    supplier = _credential_supplier(db, supplier_id)
    ready, source = supplier_key_status(db, supplier)
    return {
        "supplier_id": supplier.id,
        "configured": ready,
        "source": source,
        "encryption_ready": credential_encryption_ready(),
    }


@router.put("/{supplier_id}/credential")
async def update_supplier_credential_by_id(
    supplier_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    """按供应商录入 Key：每个池各自一把，互不覆盖。"""
    supplier = _credential_supplier(db, supplier_id)
    value = await _read_credential_body(request)
    return _save_credential(db, user, value, supplier)


@router.delete("/{supplier_id}/credential")
def delete_supplier_credential_by_id(
    supplier_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    supplier = _credential_supplier(db, supplier_id)
    try:
        clear_api_key(db, user, supplier=supplier)
        db.commit()
    except SupplierCredentialError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "SUPPLIER_CREDENTIAL_NOT_FOUND", "message": str(exc)}) from exc
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail={"code": "SUPPLIER_CREDENTIAL_CLEAR_FAILED", "message": "凭据清除失败，请重试"}) from None
    ready, source = supplier_key_status(db, supplier)
    return {"configured": ready, "source": source}


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
    suppliers = db.scalars(select(Supplier).order_by(Supplier.id)).all()
    rows = []
    for item in suppliers:
        ready, source = supplier_key_status(db, item)
        rows.append({
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "status": item.status,
            "base_url": item.base_url,
            "api_key_configured": ready,
            "credential_source": source,
            "credential_encryption_ready": credential_encryption_ready(),
            "auto_purchase_enabled": bool(item.auto_purchase_enabled),
        })
    return rows


@router.get("/pools")
def list_supplier_pools(db: Session = Depends(get_db),
                        _: User = Depends(require_roles("super_admin", "admin"))):
    """后台资源池总览：有几家上游、各在哪个池、优先级、凭据与同步状态。

    双上游阶段的核心诉求是"后台能一眼看到资源池 1 / 资源池 2"，所以这里返回
    池 + 成员 + 每家自己的商品/国家/城市数与最近同步，不把两家混成一个数字。
    """
    ensure_pool_one(db)
    db.commit()
    pools = db.scalars(select(ResourcePool).order_by(ResourcePool.id)).all()
    items = []
    for pool in pools:
        members = db.execute(
            select(ResourcePoolSupplier, Supplier)
            .join(Supplier, Supplier.id == ResourcePoolSupplier.supplier_id)
            .where(ResourcePoolSupplier.pool_id == pool.id)
            .order_by(ResourcePoolSupplier.priority, Supplier.id)
        ).all()
        supplier_views = []
        for member, supplier in members:
            last_run = db.scalar(
                select(SupplierSyncRun).where(SupplierSyncRun.supplier_id == supplier.id)
                .order_by(SupplierSyncRun.started_at.desc()).limit(1)
            )
            key_ready, key_source = supplier_key_status(db, supplier)
            supplier_views.append({
                "id": supplier.id,
                "code": supplier.code,
                "name": supplier.name,
                "adapter_type": supplier.adapter_type,
                "base_url": supplier.base_url,
                "status": supplier.status,
                "priority": member.priority,
                "is_enabled": member.is_enabled,
                "auto_purchase_enabled": bool(supplier.auto_purchase_enabled),
                "api_key_configured": key_ready,
                "credential_source": key_source,
                "counts": {
                    "products": db.scalar(select(func.count()).select_from(SupplierProduct)
                                          .where(SupplierProduct.supplier_id == supplier.id)) or 0,
                    "countries": db.scalar(select(func.count()).select_from(SupplierRegion).where(
                        SupplierRegion.supplier_product_id.in_(
                            select(SupplierProduct.id).where(SupplierProduct.supplier_id == supplier.id)
                        ),
                        SupplierRegion.city_name.is_(None),
                    )) or 0,
                    "cities": db.scalar(select(func.count()).select_from(SupplierRegion).where(
                        SupplierRegion.supplier_product_id.in_(
                            select(SupplierProduct.id).where(SupplierProduct.supplier_id == supplier.id)
                        ),
                        SupplierRegion.city_name.is_not(None),
                    )) or 0,
                },
                "last_sync_at": (last_run.finished_at or last_run.started_at) if last_run else None,
                "last_sync_status": last_run.status if last_run else "not_synced",
            })
        items.append({
            "id": pool.id,
            "code": pool.code,
            "name": pool.name,
            "status": pool.status,
            "strategy": pool.strategy,
            "suppliers": supplier_views,
        })
    purchase_enabled, purchase_source = get_flag(db, AUTO_PROCUREMENT_KEY)
    return {
        "items": items,
        "purchase_enabled": purchase_enabled,
        # runtime=后台改的；env=还没在后台改过，取环境变量默认值
        "purchase_source": purchase_source,
        "min_margin_percent": get_settings().min_order_margin_percent,
    }


class AutoProcurementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.put("/auto-procurement")
def update_auto_procurement(
    payload: AutoProcurementIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    """后台开关：付款后是否自动向上游采购。

    打开就是真扣上游余额，所以每次改动都写审计；关闭立即生效，不需要重启容器。
    """
    set_flag(db, AUTO_PROCUREMENT_KEY, payload.enabled, user)
    db.commit()
    value, source = get_flag(db, AUTO_PROCUREMENT_KEY)
    return {"enabled": value, "source": source}


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
            "api_key_configured": api_key_configured(db),
            "credential_source": credential_source(db),
            "credential_encryption_ready": credential_encryption_ready(),
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


@router.post("/sync/catalog")
def sync_supplier_catalog_endpoint(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    result = sync_supplier_catalog(db)
    write_audit(
        db, user, "sync_catalog", "supplier_catalog",
        detail=f"创建官网商品={result['created']}; 自动关联地区={result['mapped_regions']}; 缺少售价跳过={result['skipped_no_price']}; 自动采购保持关闭",
    )
    db.commit()
    return {**result, "stock_default": "out_of_stock", "procurement_enabled": False}


@router.post("/sync/countries")
def sync_supplier_countries(
    product_type_id: int = Query(default=NATIVE_PRODUCT_TYPE_ID),
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
    product_type_id: int = Query(default=NATIVE_PRODUCT_TYPE_ID),
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


@router.post("/sync/city-segments")
def sync_supplier_city_segments(
    product_type_id: int = Query(default=NATIVE_PRODUCT_TYPE_ID, ge=1, le=20),
    country_code: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
    adapter: GlobalProxyReadOnlyAdapter = Depends(get_global_proxy_adapter),
):
    """把上游「城市 + IP 段」同步进客户可选的城市清单。

    资源池 1 的 ipStrList 是按城市给段的（前两段），同步后客户在选购页就能直接选
    该城市的真实号段，而不是手工维护的示例段。可重复执行；匹配不到已有城市码的
    上游城市先落库为停用（待归属），不会直接出现在客户下拉里。
    """
    codes = [item.strip().upper() for item in (country_code or "").split(",") if item.strip()]
    run = begin_sync_run(db, user, "city_segments")
    try:
        result = sync_city_segments(db, adapter, product_type_id=product_type_id,
                                    country_codes=codes or None)
        write_audit(db, user, "sync", "catalog_city_options",
                    detail=f"类型=城市号段; 产品={product_type_id}; 国家={','.join(codes) or '全部'}; "
                           f"城市={result.cities_seen}; 段={result.segments_saved}")
        db.commit()
        completed = finish_sync_run(
            db, user, run.id,
            "partial_failed" if result.errors else "success",
            result.cities_seen, result.segments_saved,
            error_code="CITY_SEGMENT_PARTIAL" if result.errors else None,
            error_message="; ".join(result.errors) if result.errors else None,
        )
        return {**result.as_dict(), "run_id": completed.id, "status": completed.status}
    except SupplierConfigurationError as exc:
        fail_sync_run(db, user, run.id, "SUPPLIER_CONFIGURATION_ERROR", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SupplierError as exc:
        fail_sync_run(db, user, run.id, exc.code, str(exc))
        raise supplier_error(exc) from exc


@router.post("/{supplier_id}/sync-985")
def sync_pool_985(
    supplier_id: int,
    country_code: str | None = Query(default=None, max_length=80),
    static_proxy_type: str = Query(default="premium", pattern="^(premium|shared)$"),
    fetch_segments: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    """资源池 2（985Proxy）：拉库存 → 写入客户可选城市（含号段）。

    先查一次钱包余额，Key 错误或实名未通过会在这里就报出来，不用等同步跑一半才失败。
    """
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if supplier.adapter_type != "nine_eight_five":
        raise HTTPException(status_code=409, detail="该资源池不是 985Proxy")

    adapter = build_supplier_adapter(db, supplier)
    run = begin_sync_run(db, user, "cities_985")
    try:
        wallet = adapter.wallet_balance()
        codes = [item.strip().upper() for item in (country_code or "").split(",") if item.strip()]
        result = sync_city_segments_985(
            db, adapter, country_codes=codes or None,
            static_proxy_type=static_proxy_type, fetch_segments=fetch_segments,
        )
        write_audit(db, user, "sync", "catalog_city_options",
                    detail=f"来源=985; 类型={static_proxy_type}; 城市={result.cities_seen}; "
                           f"段={result.segments_saved}; 国家={','.join(codes) or '全部'}")
        db.commit()
        completed = finish_sync_run(
            db, user, run.id,
            "partial_failed" if result.errors else "success",
            result.cities_seen, result.segments_saved,
            error_code="CITY_985_PARTIAL" if result.errors else None,
            error_message="; ".join(result.errors) if result.errors else None,
        )
        return {**result.as_dict(), "run_id": completed.id, "status": completed.status,
                "wallet": wallet}
    except SupplierConfigurationError as exc:
        fail_sync_run(db, user, run.id, "SUPPLIER_CONFIGURATION_ERROR", str(exc))
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


@router.get("/ip-segments")
def list_supplier_ip_segments(
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    resource_category: str | None = None,
    supplier_product_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=300, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    settings = get_settings()
    query = select(SupplierIpSegmentSnapshot).join(
        SupplierProduct, SupplierProduct.id == SupplierIpSegmentSnapshot.supplier_product_id,
    ).order_by(SupplierIpSegmentSnapshot.checked_at.desc())
    if country_code:
        query = query.where(SupplierIpSegmentSnapshot.country_code == country_code.upper())
    if supplier_product_id:
        query = query.where(SupplierIpSegmentSnapshot.supplier_product_id == supplier_product_id)
    if resource_category:
        query = query.where(SupplierProduct.resource_category == resource_category)
    query = query.limit(limit)
    records = db.scalars(query).all()
    products = {item.id: item for item in db.scalars(select(SupplierProduct).where(
        SupplierProduct.id.in_({item.supplier_product_id for item in records})
    )).all()} if records else {}
    return [{
        **serialize_model(item),
        "resource_category": products.get(item.supplier_product_id).resource_category if products.get(item.supplier_product_id) else None,
        "data_state": snapshot_state(db, item.checked_at, {"inventory_check"}, settings.supplier_inventory_ttl_minutes),
    } for item in records]


@router.get("/resource-categories")
def list_resource_categories(_: User = Depends(require_roles("super_admin", "admin"))):
    return [{"code": code, "label": label} for code, label in RESOURCE_CATEGORY_CODES.items()]


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
        segment_records = save_ip_segment_snapshots(
            db,
            payload.product_type_id,
            payload.country_code,
            payload.quantity,
            payload.duration_code,
            result.segments,
            result.is_sufficient,
            run.id,
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
            "segments": [{"id": item.id, "cidr": item.cidr, "is_sufficient": item.is_segment_sufficient} for item in segment_records],
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
        record = map_supplier_product(db, supplier_product_id, payload.local_product_id, payload.resource_category)
        write_audit(db, user, "update", "supplier_product_mapping", record.id, f"玖玖云商品={payload.local_product_id}; 资源分类={payload.resource_category}")
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
