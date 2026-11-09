from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.data.site_catalog import ACTIVE_CATEGORIES
from app.models import Product, Region, RetailPriceRule, User
from app.services.audit_service import write_audit
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
