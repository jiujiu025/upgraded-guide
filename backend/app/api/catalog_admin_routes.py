from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.data.site_catalog import ACTIVE_CATEGORIES, CATEGORY_TO_LINE, LINE_LABELS
from app.models import CatalogCityOption, CatalogListing, Product, Region, ResourcePool, RetailPriceRule, User
from app.services.audit_service import write_audit
from app.services.city_option_service import (
    normalize_city_code, normalize_segments, option_view, segments_text,
)
from app.services.pricing_service import BASE_PERIOD_DAYS, sync_display_price
from app.services.supplier_service import RESOURCE_CATEGORY_CODES


router = APIRouter(prefix="/admin/catalog-management", tags=["管理员商品与价格"])


class ProductUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    is_in_stock: bool | None = None


class ProductBulkUpdateIn(ProductUpdateIn):
    product_ids: list[int] = Field(min_length=1, max_length=200)


class RetailPriceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class RetailPriceBulkUpdateIn(RetailPriceUpdateIn):
    price_rule_ids: list[int] = Field(min_length=1, max_length=500)


def _product_view(product: Product, region_name: str | None) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "product_type": product.product_type,
        "price": round(float(product.price), 2),
        "billing_days": product.billing_days,
        "region_id": product.region_id,
        "region_name": region_name,
        "is_in_stock": product.is_in_stock,
        "is_active": product.is_active,
        "resource_category": product.resource_category,
        "price_locked": bool(product.resource_category),
        "updated_at": product.updated_at,
    }


def _validate_product_update(changes: dict) -> None:
    if not changes:
        raise HTTPException(status_code=422, detail="请选择需要修改的售价或库存状态")
    if "price" in changes and changes["price"] is None:
        raise HTTPException(status_code=422, detail="售价不能为空")
    if "is_in_stock" in changes and changes["is_in_stock"] is None:
        raise HTTPException(status_code=422, detail="库存状态不能为空")


def _reject_direct_price_edit(product: Product, changes: dict) -> None:
    """归入资源分类的商品，价格由零售价目表的月价线性决定。

    直接改 Product.price 既不会生效、又会让人误以为改成功了，所以这里直接拒绝，
    把改价入口收敛到「零售价」页。
    """
    if "price" not in changes or not product.resource_category:
        return
    raise HTTPException(
        status_code=400,
        detail="该商品价格由零售价目表决定（月价 × 周期数），请到「零售价」页修改月价",
    )


def _reject_long_period_price_edit(item: RetailPriceRule) -> None:
    """长周期价由月价派生，不允许单独配置，否则会出现第二份价格。"""
    if item.billing_days != BASE_PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"{item.billing_days} 天价格由月价自动计算（{item.billing_days // BASE_PERIOD_DAYS} 倍），请修改 30 天档月价",
        )


def _sync_products_of_rule(db: Session, actor: User, rule: RetailPriceRule) -> None:
    """月价变更后，把派生价回写到该分类 + 该国家下所有商品的展示价。"""
    if rule.billing_days != BASE_PERIOD_DAYS:
        return
    products = db.scalars(select(Product).where(
        Product.resource_category == rule.resource_category,
        Product.region_id.in_(select(Region.id).where(Region.country_code == rule.country_code.strip().upper())),
    )).all()
    for product in products:
        if sync_display_price(db, product):
            write_audit(db, actor, "sync_display_price", "product", product.id,
                        f"跟随月价回写={product.price}")


@router.get("/products")
def list_catalog_products(
    keyword: str | None = Query(default=None, max_length=120),
    stock: str | None = Query(default=None, pattern="^(in_stock|out_of_stock)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(Product, Region.name).join(Region, Region.id == Product.region_id)
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.where(Product.name.ilike(pattern) | Product.product_type.ilike(pattern) | Region.name.ilike(pattern))
    if stock == "in_stock":
        query = query.where(Product.is_in_stock.is_(True))
    elif stock == "out_of_stock":
        query = query.where(Product.is_in_stock.is_(False))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(Product.name, Product.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [_product_view(product, region_name) for product, region_name in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/products/{product_id}")
def update_catalog_product(
    product_id: int,
    payload: ProductUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    changes = payload.model_dump(exclude_unset=True)
    _validate_product_update(changes)
    _reject_direct_price_edit(product, changes)
    old_values = {field: getattr(product, field) for field in changes}
    for field, value in changes.items():
        setattr(product, field, float(value) if field == "price" else value)
    detail = "; ".join(f"{field}={old_values[field]}→{getattr(product, field)}" for field in changes)
    write_audit(db, actor, "update_catalog_product", "product", product.id, detail)
    db.commit()
    region = db.get(Region, product.region_id)
    return _product_view(product, region.name if region else None)


@router.post("/products/bulk")
def bulk_update_catalog_products(
    payload: ProductBulkUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    changes = payload.model_dump(exclude={"product_ids"}, exclude_unset=True)
    _validate_product_update(changes)
    ids = list(dict.fromkeys(payload.product_ids))
    if len(ids) != len(payload.product_ids):
        raise HTTPException(status_code=422, detail="商品选择列表包含重复项目")
    products = db.scalars(select(Product).where(Product.id.in_(ids)).order_by(Product.id)).all()
    if len(products) != len(ids):
        raise HTTPException(status_code=404, detail="部分商品已不存在，未执行批量修改")
    for product in products:
        _reject_direct_price_edit(product, changes)
    for product in products:
        old_values = {field: getattr(product, field) for field in changes}
        for field, value in changes.items():
            setattr(product, field, float(value) if field == "price" else value)
        detail = "; ".join(f"{field}={old_values[field]}→{getattr(product, field)}" for field in changes)
        write_audit(db, actor, "bulk_update_catalog_product", "product", product.id,
                    f"批量操作，共 {len(products)} 个商品；{detail}")
    db.commit()
    return {"updated_count": len(products)}


@router.get("/retail-prices")
def list_retail_prices(
    resource_category: str | None = Query(default=None, max_length=50),
    country_code: str | None = Query(default=None, max_length=10),
    billing_days: int | None = Query(default=None, ge=30, le=3650),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(RetailPriceRule)
    if resource_category:
        query = query.where(RetailPriceRule.resource_category == resource_category)
    if country_code and country_code.strip():
        query = query.where(RetailPriceRule.country_code == country_code.strip().upper())
    if billing_days is not None:
        query = query.where(RetailPriceRule.billing_days == billing_days)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(RetailPriceRule.resource_category, RetailPriceRule.country_code,
                                     RetailPriceRule.billing_days)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    countries = db.scalars(
        select(RetailPriceRule.country_code).distinct().order_by(RetailPriceRule.country_code)
    ).all()
    # 国家中文名以 Region 为准（"中国台湾 / 中国香港"这类口径不能靠前端 Intl 猜）。
    region_names = dict(db.execute(select(Region.country_code, Region.name)).all())
    billing_days_options = db.scalars(
        select(RetailPriceRule.billing_days).distinct().order_by(RetailPriceRule.billing_days)
    ).all()
    return {
        "items": [{
            "id": item.id,
            "resource_category": item.resource_category,
            "category_label": RESOURCE_CATEGORY_CODES.get(item.resource_category, item.resource_category),
            "country_code": item.country_code,
            "country_name": region_names.get(item.country_code, item.country_code),
            "billing_days": item.billing_days,
            "unit_price": str(item.unit_price),
            "currency": item.currency,
            "source_version": item.source_version,
            "updated_at": item.updated_at,
        } for item in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "categories": [
            {
                "code": code,
                # 已停售的分类照样列出（历史价则还要能查、能改），但加后缀提醒：
                # 往这些分类写新价不会出现在官网上，避免运营白填一遍。
                "label": label if code in ACTIVE_CATEGORIES else f"{label}（已停售，不上官网）",
                "active": code in ACTIVE_CATEGORIES,
            }
            for code, label in RESOURCE_CATEGORY_CODES.items()
        ],
        "countries": [{"code": code, "name": region_names.get(code, code)} for code in countries],
        "billing_days_options": billing_days_options,
    }


@router.patch("/retail-prices/{price_rule_id}")
def update_retail_price(
    price_rule_id: int,
    payload: RetailPriceUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    item = db.get(RetailPriceRule, price_rule_id)
    if not item:
        raise HTTPException(status_code=404, detail="零售价规则不存在")
    _reject_long_period_price_edit(item)
    before = item.unit_price
    item.unit_price = payload.unit_price
    write_audit(db, actor, "update_retail_price", "retail_price_rule", item.id,
                f"售价={before}→{item.unit_price}; 类型={item.resource_category}; 国家={item.country_code}; 周期={item.billing_days}")
    _sync_products_of_rule(db, actor, item)
    db.commit()
    return {"id": item.id, "unit_price": str(item.unit_price), "updated_at": item.updated_at}


@router.post("/retail-prices/bulk")
def bulk_update_retail_prices(
    payload: RetailPriceBulkUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    ids = list(dict.fromkeys(payload.price_rule_ids))
    if len(ids) != len(payload.price_rule_ids):
        raise HTTPException(status_code=422, detail="价格选择列表包含重复项目")
    rows = db.scalars(select(RetailPriceRule).where(RetailPriceRule.id.in_(ids)).order_by(RetailPriceRule.id)).all()
    if len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="部分价格规则已不存在，未执行批量修改")
    for item in rows:
        _reject_long_period_price_edit(item)
    for item in rows:
        before = item.unit_price
        item.unit_price = payload.unit_price
        write_audit(db, actor, "bulk_update_retail_price", "retail_price_rule", item.id,
                    f"批量操作，共 {len(rows)} 条；售价={before}→{item.unit_price}; 类型={item.resource_category}; 国家={item.country_code}; 周期={item.billing_days}")
    for item in rows:
        _sync_products_of_rule(db, actor, item)
    db.commit()
    return {"updated_count": len(rows)}


# ---------------------------------------------------------------------------
# 官网可见性：产品线 × 国家 的上架/下架
#
# 缺价的国家（社媒 11 国、静态俄罗斯）没有商品行，没法用 products.is_active 控制，
# 所以单独记一层覆盖。默认规则是"有价即上架"，这里只存人工改过的格子。
# ---------------------------------------------------------------------------

LISTING_STATUSES = {"listed", "unlisted"}
SITE_LINE_CODES = ("residential", "social", "live")


class ListingUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_code: str = Field(pattern="^(residential|social|live)$")
    region_id: int = Field(gt=0)
    # 空 = 平台级（所有池生效）；非空 = 只改这个池
    pool_id: int | None = Field(default=None, gt=0)
    status: str
    reason: str | None = Field(default=None, max_length=200)

    def validated_status(self) -> str:
        if self.status not in LISTING_STATUSES:
            raise HTTPException(status_code=422, detail="状态只能是 listed 或 unlisted")
        return self.status


class ListingBulkUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # line_code 为空表示三条线一起改；pool_id 为空表示平台级
    line_code: str | None = Field(default=None, pattern="^(residential|social|live)$")
    pool_id: int | None = Field(default=None, gt=0)
    region_ids: list[int] = Field(min_length=1, max_length=200)
    status: str
    reason: str | None = Field(default=None, max_length=200)


class ProductActiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


def _priced_cells(db: Session) -> set[tuple[str, int]]:
    """哪些 (分类, 地区) 有在售商品。分类再映射到产品线。"""
    rows = db.execute(
        select(Product.resource_category, Product.region_id)
        .where(Product.is_active.is_(True), Product.resource_category.is_not(None))
        .distinct()
    ).all()
    cells = set()
    for category, region_id in rows:
        line = CATEGORY_TO_LINE.get(category or "")
        if line:
            cells.add((line, region_id))
    return cells


def _pool_or_404(db: Session, pool_id: int | None) -> ResourcePool | None:
    if pool_id is None:
        return None
    pool = db.get(ResourcePool, pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="资源池不存在")
    return pool


@router.get("/listings")
def list_catalog_listings(pool_id: int | None = Query(default=None, ge=1),
                          db: Session = Depends(get_db),
                          _: User = Depends(require_roles("super_admin", "admin"))):
    """官网可见性网格：池 × 产品线 × 国家。

    **默认全部下架**：没有记录的格子就是不可见，必须管理员点开。
    指定 pool_id 时，该池有记录用池级状态，否则回退到平台级（pool_id 为空的记录）。
    """
    current_pool = _pool_or_404(db, pool_id)
    pools = db.scalars(select(ResourcePool).order_by(ResourcePool.id)).all()
    regions = db.scalars(select(Region).where(Region.is_active.is_(True)).order_by(Region.name)).all()
    priced = _priced_cells(db)

    global_rows = {
        (row.line_code, row.region_id): row
        for row in db.scalars(select(CatalogListing).where(CatalogListing.pool_id.is_(None))).all()
    }
    pool_rows = {}
    if pool_id is not None:
        pool_rows = {
            (row.line_code, row.region_id): row
            for row in db.scalars(select(CatalogListing).where(CatalogListing.pool_id == pool_id)).all()
        }

    cells = []
    for line in SITE_LINE_CODES:
        for region in regions:
            key = (line, region.id)
            record = pool_rows.get(key) or global_rows.get(key)
            if record is None:
                status, source, reason = "unlisted", "none", None
            else:
                status = record.status
                source = "pool" if key in pool_rows else "global"
                reason = record.reason
            cells.append({
                "line_code": line,
                "line_name": LINE_LABELS[line],
                "region_id": region.id,
                "region_name": region.name,
                "country_code": region.country_code,
                "has_price": (line, region.id) in priced,
                "status": status,
                "source": source,
                "reason": reason,
                "visible": status == "listed",
            })

    listed = sum(1 for cell in cells if cell["visible"])
    return {
        "lines": [{"code": line, "name": LINE_LABELS[line]} for line in SITE_LINE_CODES],
        "regions": [{"id": region.id, "name": region.name, "country_code": region.country_code}
                    for region in regions],
        "pools": [{"id": pool.id, "code": pool.code, "name": pool.name, "status": pool.status}
                  for pool in pools],
        "scope": {
            "pool_id": pool_id,
            "pool_name": current_pool.name if current_pool else "平台级（全池）",
        },
        "cells": cells,
        "summary": {
            "total": len(cells),
            "listed": listed,
            "unlisted": len(cells) - listed,
            # 没有报价、却被上架的格子：展示"暂无报价"，属于管理员的有意决定
            "missing_price": sum(1 for cell in cells if not cell["has_price"]),
            "manual": sum(1 for cell in cells if cell["source"] != "none"),
        },
    }


def _upsert_listing(db: Session, actor: User, line_code: str, region_id: int, pool_id: int | None,
                    status: str, reason: str | None) -> CatalogListing:
    region = db.get(Region, region_id)
    if region is None or not region.is_active:
        raise HTTPException(status_code=404, detail="国家不存在或已停用")
    _pool_or_404(db, pool_id)
    query = select(CatalogListing).where(
        CatalogListing.line_code == line_code,
        CatalogListing.region_id == region_id,
    )
    query = query.where(CatalogListing.pool_id == pool_id) if pool_id is not None \
        else query.where(CatalogListing.pool_id.is_(None))
    record = db.scalar(query)
    if record is None:
        record = CatalogListing(line_code=line_code, region_id=region_id, pool_id=pool_id,
                                status=status, reason=reason, updated_by=actor.id)
        db.add(record)
    else:
        record.status = status
        record.reason = reason
        record.updated_by = actor.id
    db.flush()
    return record


@router.patch("/listings")
def update_catalog_listing(payload: ListingUpdateIn, db: Session = Depends(get_db),
                           user: User = Depends(require_roles("super_admin", "admin"))):
    status = payload.validated_status()
    record = _upsert_listing(db, user, payload.line_code, payload.region_id, payload.pool_id,
                             status, payload.reason)
    region = db.get(Region, payload.region_id)
    pool = _pool_or_404(db, payload.pool_id)
    write_audit(db, user, "update_listing", "catalog_listing", record.id,
                f"线={payload.line_code}; 国家={region.country_code if region else payload.region_id}; "
                f"池={pool.name if pool else '平台级'}; 状态={status}")
    db.commit()
    return {"line_code": record.line_code, "region_id": record.region_id,
            "pool_id": record.pool_id, "status": record.status, "reason": record.reason,
            "source": "pool" if record.pool_id else "global"}


@router.post("/listings/bulk")
def bulk_update_catalog_listings(payload: ListingBulkUpdateIn, db: Session = Depends(get_db),
                                 user: User = Depends(require_roles("super_admin", "admin"))):
    if payload.status not in LISTING_STATUSES:
        raise HTTPException(status_code=422, detail="状态只能是 listed 或 unlisted")
    lines = [payload.line_code] if payload.line_code else list(SITE_LINE_CODES)
    region_ids = list(dict.fromkeys(payload.region_ids))
    pool = _pool_or_404(db, payload.pool_id)
    changed = 0
    for line in lines:
        for region_id in region_ids:
            _upsert_listing(db, user, line, region_id, payload.pool_id, payload.status, payload.reason)
            changed += 1
    write_audit(db, user, "bulk_update_listing", "catalog_listing", None,
                f"线={payload.line_code or '全部'}; 池={pool.name if pool else '平台级'}; "
                f"国家数={len(region_ids)}; 状态={payload.status}; 影响={changed}")
    db.commit()
    return {"updated": changed, "lines": lines, "regions": len(region_ids),
            "status": payload.status, "pool_id": payload.pool_id}


@router.patch("/products/{product_id}/active")
def set_product_active(product_id: int, payload: ProductActiveIn, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("super_admin", "admin"))):
    """商品级上下架：停用后该商品在所有国家/线路都不再出现。"""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    if product.is_active == payload.is_active:
        return {"id": product.id, "is_active": product.is_active, "changed": False}
    product.is_active = payload.is_active
    write_audit(db, user, "toggle_product_active", "product", product.id,
                f"上下架={payload.is_active}")
    db.commit()
    return {"id": product.id, "is_active": product.is_active, "changed": True}


# ---------------------------------------------------------------------------
# 城市与号段：客户可选的精细化选项（城市码 + 可选 IP 段）
#
# 官网只到国家，城市是内部参数；但做 TikTok / 支付 / 电商的客户经常指定城市或号段。
# 这里维护「某分类 + 某国家」下可售的城市清单，客户下单时只能从中选。
# 设计见 docs/城市与IP段选择设计.md。
# ---------------------------------------------------------------------------


class CityOptionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_category: str = Field(min_length=1, max_length=50)
    country_code: str = Field(min_length=2, max_length=10)
    city_code: str = Field(min_length=1, max_length=32)
    city_name: str = Field(min_length=1, max_length=80)
    ip_segments: str = Field(default="", max_length=500)
    is_enabled: bool = True
    sort_order: int = Field(default=100, ge=0, le=9999)
    note: str | None = Field(default=None, max_length=200)


class CityOptionUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city_code: str | None = Field(default=None, min_length=1, max_length=32)
    city_name: str | None = Field(default=None, min_length=1, max_length=80)
    ip_segments: str | None = Field(default=None, max_length=500)
    is_enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    note: str | None = Field(default=None, max_length=200)


def _validate_city_target(db: Session, resource_category: str, country_code: str) -> str:
    if resource_category not in RESOURCE_CATEGORY_CODES:
        raise HTTPException(status_code=422, detail="资源类型不存在")
    code = country_code.strip().upper()
    region = db.scalar(select(Region).where(Region.country_code == code, Region.is_active.is_(True)))
    if region is None:
        raise HTTPException(status_code=404, detail="国家/地区不存在或已停用")
    return code


def _parse_city_fields(payload: dict) -> dict:
    """把城市码与号段文本归一化，格式问题统一回 422。"""
    changes = dict(payload)
    try:
        if "city_code" in changes and changes["city_code"] is not None:
            changes["city_code"] = normalize_city_code(changes["city_code"])
            if not changes["city_code"]:
                raise ValueError("城市码不能为空")
        if "ip_segments" in changes and changes["ip_segments"] is not None:
            changes["ip_segments"] = segments_text(normalize_segments(changes["ip_segments"]))
        if "city_name" in changes and changes["city_name"] is not None:
            changes["city_name"] = changes["city_name"].strip()
            if not changes["city_name"]:
                raise ValueError("城市名称不能为空")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return changes


def _city_view(db: Session, option: CatalogCityOption, region_names: dict[str, str]) -> dict:
    return {
        **option_view(option),
        "country_name": region_names.get(option.country_code, option.country_code),
        "category_label": RESOURCE_CATEGORY_CODES.get(option.resource_category, option.resource_category),
    }


@router.get("/cities")
def list_city_options(
    resource_category: str | None = Query(default=None, max_length=50),
    country_code: str | None = Query(default=None, max_length=10),
    keyword: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(CatalogCityOption)
    if resource_category:
        query = query.where(CatalogCityOption.resource_category == resource_category)
    if country_code and country_code.strip():
        query = query.where(CatalogCityOption.country_code == country_code.strip().upper())
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.where(CatalogCityOption.city_name.ilike(pattern) | CatalogCityOption.city_code.ilike(pattern))
    rows = db.scalars(query.order_by(
        CatalogCityOption.country_code, CatalogCityOption.sort_order, CatalogCityOption.city_code,
    ).limit(500)).all()
    region_names = dict(db.execute(select(Region.country_code, Region.name)).all())
    # 只列有商品在售的国家，避免运营给一个根本不卖的国家配城市。
    countries = db.execute(
        select(Region.country_code, Region.name, func.min(Region.id))
        .where(Region.is_active.is_(True))
        .group_by(Region.country_code, Region.name)
        .order_by(Region.name)
    ).all()
    return {
        "items": [_city_view(db, item, region_names) for item in rows],
        "total": len(rows),
        "categories": [
            {"code": code, "label": label, "active": code in ACTIVE_CATEGORIES}
            for code, label in RESOURCE_CATEGORY_CODES.items()
        ],
        "countries": [
            {"code": code, "name": name or region_names.get(code, code)}
            for code, name, _ in countries
        ],
    }


@router.post("/cities", status_code=201)
def create_city_option(
    payload: CityOptionCreateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    country_code = _validate_city_target(db, payload.resource_category, payload.country_code)
    fields = _parse_city_fields(payload.model_dump())
    fields["country_code"] = country_code
    duplicated = db.scalar(select(CatalogCityOption).where(
        CatalogCityOption.resource_category == fields["resource_category"],
        CatalogCityOption.country_code == country_code,
        CatalogCityOption.city_code == fields["city_code"],
    ))
    if duplicated is not None:
        raise HTTPException(status_code=409, detail="该国家下已有相同城市码，请直接编辑那一条")
    option = CatalogCityOption(**fields)
    db.add(option)
    db.flush()
    write_audit(db, actor, "create", "catalog_city_option", option.id,
                f"城市={option.city_code}; 国家={country_code}; 号段={option.ip_segments or '无'}")
    db.commit()
    region_names = dict(db.execute(select(Region.country_code, Region.name)).all())
    return _city_view(db, option, region_names)


@router.patch("/cities/{city_option_id}")
def update_city_option(
    city_option_id: int,
    payload: CityOptionUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    option = db.get(CatalogCityOption, city_option_id)
    if option is None:
        raise HTTPException(status_code=404, detail="城市配置不存在")
    changes = _parse_city_fields(payload.model_dump(exclude_unset=True))
    if not changes:
        raise HTTPException(status_code=422, detail="没有需要修改的内容")
    if "city_code" in changes and changes["city_code"] != option.city_code:
        duplicated = db.scalar(select(CatalogCityOption).where(
            CatalogCityOption.resource_category == option.resource_category,
            CatalogCityOption.country_code == option.country_code,
            CatalogCityOption.city_code == changes["city_code"],
        ))
        if duplicated is not None:
            raise HTTPException(status_code=409, detail="该国家下已有相同城市码")
    before = {field: getattr(option, field) for field in changes}
    for field, value in changes.items():
        setattr(option, field, value)
    detail = "; ".join(f"{field}={before[field]}→{getattr(option, field)}" for field in changes)
    write_audit(db, actor, "update", "catalog_city_option", option.id, detail)
    db.commit()
    region_names = dict(db.execute(select(Region.country_code, Region.name)).all())
    return _city_view(db, option, region_names)


@router.delete("/cities/{city_option_id}", status_code=204)
def delete_city_option(
    city_option_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    option = db.get(CatalogCityOption, city_option_id)
    if option is None:
        raise HTTPException(status_code=404, detail="城市配置不存在")
    write_audit(db, actor, "delete", "catalog_city_option", option.id,
                f"城市={option.city_code}; 国家={option.country_code}")
    db.delete(option)
    db.commit()
