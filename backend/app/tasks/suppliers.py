"""供应商上游数据自动刷新。

背景：``SUPPLIER_CATALOG_TTL_MINUTES`` 这几项配置此前只被**读取**——用来判断
快照是否新鲜、发布前校验是否过期——但没有任何东西在刷新上游数据。运维不点
"同步"按钮，数据就永久过期，而发布目录会因为"快照已过期"被直接拒绝。

三个任务：

- ``refresh_catalog``   产品 / 国家 / 城市，各自按 TTL 决定要不要拉
- ``refresh_inventory`` 按国家轮转，每轮只拉一批，避免打爆上游接口
- ``refresh_quotes``    完整单价表

## 为什么自动刷新不写 SupplierSyncRun

``SupplierSyncRun.status`` 是**发布闸门**：``catalog_publication_service._require_fresh``
看到某个 sync_type 最近一次不是 success 就禁止发布。自动任务一轮要拉十来个国家，
只要其中一个国家报错，就会把整个人工发布流程卡死——而且运维根本不知道该修哪个
国家。所以自动刷新只更新快照时间（``last_synced_at`` / ``checked_at`` /
``quoted_at``），让数据重新变"新鲜"，执行结果记在 ``scheduled_task_runs``
（后台 ``/admin/tasks`` 可见）。人工同步仍然照旧写 ``SupplierSyncRun``。
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    CatalogPublication, SupplierInventorySnapshot, SupplierProduct, SupplierQuote, SupplierRegion,
)
from app.services.supplier_credentials import SupplierCredentialError, has_configured_api_key
from app.services.supplier_service import (
    CITIES_ENDPOINT, COUNTRIES_ENDPOINT, ensure_pool_one, save_inventory, save_quotes,
    sync_products, sync_regions,
)
from app.suppliers.base import SupplierError
from app.suppliers.factory import build_readonly_adapter
from app.suppliers.global_proxy import SUPPORTED_REGION_PRODUCT_ID

# 每轮最多拉多少个国家的库存。太小会让国家长期停在 stale，太大容易触发上游限流。
MAX_BATCH = 50


def _stale(observed_at: datetime | None, now: datetime, ttl_minutes: int) -> bool:
    return observed_at is None or observed_at < now - timedelta(minutes=ttl_minutes)


def _adapter_or_skip(db: Session, adapter):
    """返回 (适配器, 跳过原因)。显式传入适配器时跳过密钥检查，便于测试。"""
    if adapter is not None:
        return adapter, None
    if not has_configured_api_key(db):
        return None, "未配置供应商 API Key"
    try:
        return build_readonly_adapter(db), None
    except SupplierCredentialError as exc:
        return None, str(exc)


def refresh_catalog(db: Session, *, now: datetime, force: bool = False, adapter=None) -> dict:
    settings = get_settings()
    supplier, _ = ensure_pool_one(db)
    db.flush()

    last_product = db.scalar(
        select(func.max(SupplierProduct.last_synced_at)).where(SupplierProduct.supplier_id == supplier.id)
    )
    last_country = db.scalar(
        select(func.max(SupplierRegion.last_synced_at)).where(SupplierRegion.source_endpoint == COUNTRIES_ENDPOINT)
    )
    last_city = db.scalar(
        select(func.max(SupplierRegion.last_synced_at)).where(SupplierRegion.source_endpoint == CITIES_ENDPOINT)
    )

    ttl = settings.supplier_catalog_ttl_minutes
    plan = [
        name for name, observed in (
            ("products", last_product),
            ("countries", last_country),
            ("cities", last_city),
        )
        if force or _stale(observed, now, ttl)
    ]
    if not plan:
        return {"products": 0, "countries": 0, "cities": 0, "note": "目录仍在有效期内，本轮跳过"}

    client, skip_reason = _adapter_or_skip(db, adapter)
    if client is None:
        return {"skipped": skip_reason, "pending_steps": ",".join(plan)}

    counts = {"products": 0, "countries": 0, "cities": 0}
    errors: dict[str, str] = {}
    created_here = adapter is None
    try:
        for step in plan:
            try:
                if step == "products":
                    counts["products"] = len(sync_products(db, client.list_products(), synced_at=now))
                elif step == "countries":
                    counts["countries"] = len(sync_regions(
                        db, SUPPORTED_REGION_PRODUCT_ID,
                        client.list_countries(SUPPORTED_REGION_PRODUCT_ID), COUNTRIES_ENDPOINT,
                        synced_at=now,
                    ))
                else:
                    counts["cities"] = len(sync_regions(
                        db, SUPPORTED_REGION_PRODUCT_ID,
                        client.list_cities(SUPPORTED_REGION_PRODUCT_ID), CITIES_ENDPOINT,
                        synced_at=now,
                    ))
                db.flush()
            except (SupplierError, ValueError) as exc:
                errors[step] = f"{type(exc).__name__}: {exc}"[:200]
            except Exception as exc:
                # 数据库层面的错误会让会话不可用，回滚后继续下一步没有意义，
                # 但仍记下来，避免一步失败变成整轮无记录。
                db.rollback()
                errors[step] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        if created_here:
            client.close()

    if len(errors) == len(plan):
        raise RuntimeError("; ".join(f"{step}={message}" for step, message in errors.items()))

    result = {**counts, "errors": len(errors)}
    if errors:
        result["failed_steps"] = ",".join(errors)
    return result


def refresh_inventory(
    db: Session,
    *,
    now: datetime,
    batch_size: int,
    stale_minutes: int,
    retention_days: int | None = None,
    adapter=None,
) -> dict:
    settings = get_settings()
    supplier, _ = ensure_pool_one(db)
    db.flush()

    product = db.scalar(select(SupplierProduct).where(
        SupplierProduct.supplier_id == supplier.id,
        SupplierProduct.external_product_ref == str(SUPPORTED_REGION_PRODUCT_ID),
    ))
    if product is None:
        return {"skipped": "尚未同步供应商商品，先执行目录刷新"}

    countries = db.scalars(select(SupplierRegion.country_code).where(
        SupplierRegion.supplier_product_id == product.id,
    ).distinct()).all()
    if not countries:
        return {"skipped": "尚未同步供应商国家，先执行目录刷新"}

    last_seen = dict(db.execute(
        select(SupplierInventorySnapshot.country_code, func.max(SupplierInventorySnapshot.checked_at))
        .where(SupplierInventorySnapshot.supplier_product_id == product.id)
        .group_by(SupplierInventorySnapshot.country_code)
    ).all())

    # 从没查过的国家排最前，其余按上次查询时间升序：轮转保证不会永远饿死某些国家。
    ordered = sorted(
        countries,
        key=lambda code: (last_seen.get(code) is not None, last_seen.get(code) or now),
    )
    stale = [code for code in ordered if _stale(last_seen.get(code), now, stale_minutes)]
    batch = stale[:max(1, min(batch_size, MAX_BATCH))]
    if not batch:
        return {"countries": 0, "pending": 0, "note": "全部国家仍在有效期内，本轮跳过"}

    client, skip_reason = _adapter_or_skip(db, adapter)
    if client is None:
        return {"skipped": skip_reason, "pending": len(stale)}

    refreshed = 0
    failures: dict[str, str] = {}
    created_here = adapter is None
    try:
        for country_code in batch:
            try:
                items = client.get_inventory(SUPPORTED_REGION_PRODUCT_ID, country_code)
                save_inventory(db, SUPPORTED_REGION_PRODUCT_ID, items, checked_at=now)
                db.flush()
                refreshed += 1
            except (SupplierError, ValueError) as exc:
                failures[country_code] = f"{type(exc).__name__}: {exc}"[:120]
    finally:
        if created_here:
            client.close()

    if refreshed == 0 and failures:
        raise RuntimeError("; ".join(f"{code}={message}" for code, message in failures.items()))

    pruned = _prune_snapshots(
        db, now=now,
        retention_days=retention_days if retention_days is not None else settings.supplier_snapshot_retention_days,
    )
    result = {
        "countries": refreshed,
        "pending": len(stale) - len(batch),
        "pruned": pruned,
        "failures": len(failures),
    }
    if failures:
        result["failed_countries"] = ",".join(sorted(failures))[:200]
    return result


def refresh_quotes(db: Session, *, now: datetime, force: bool = False, adapter=None) -> dict:
    settings = get_settings()
    last_quote = db.scalar(select(func.max(SupplierQuote.quoted_at)))
    if not force and not _stale(last_quote, now, settings.supplier_quote_ttl_minutes):
        return {"quotes": 0, "note": "报价仍在有效期内，本轮跳过"}

    client, skip_reason = _adapter_or_skip(db, adapter)
    if client is None:
        return {"skipped": skip_reason}

    created_here = adapter is None
    try:
        records = save_quotes(db, client.get_quotes(), quoted_at=now)
        db.flush()
    finally:
        if created_here:
            client.close()
    return {"quotes": len(records)}


def _prune_snapshots(db: Session, *, now: datetime, retention_days: int) -> int:
    """清理过期快照。

    库存快照每轮全量追加（不覆盖），不清理的话一天就能写进上千行；报价同理。
    已被发布记录引用的快照必须保留，否则会破坏 ``catalog_publications`` 的外键。
    """
    cutoff = now - timedelta(days=retention_days)
    inventory_deleted = db.execute(delete(SupplierInventorySnapshot).where(
        SupplierInventorySnapshot.checked_at < cutoff,
        SupplierInventorySnapshot.id.not_in(select(CatalogPublication.inventory_snapshot_id)),
    )).rowcount or 0
    quote_deleted = db.execute(delete(SupplierQuote).where(
        SupplierQuote.quoted_at < cutoff,
        SupplierQuote.id.not_in(select(CatalogPublication.quote_snapshot_id)),
    )).rowcount or 0
    return inventory_deleted + quote_deleted
