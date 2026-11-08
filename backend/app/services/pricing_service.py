"""价格唯一入口：所有对外报价必须经过这里。

口径（2026-11-08 确认）：严格线性、无折扣。
    周期单价 = 月价 × (billing_days / 30)
    即 30 天 = 1×、90 天 = 3×、180 天 = 6×、360 天 = 12×。

设计约束：
    1. 唯一真相源是 RetailPriceRule 的 30 天档，其余周期一律算出来，不查表；
    2. 折扣功能不在本期范围，未来在 quote() 里加一层调整即可，不动本文件结构；
    3. resource_category 为空表示固定价商品（如直播专线），沿用旧的系数逻辑。
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Region, RetailPriceRule

BASE_PERIOD_DAYS = 30
SELLABLE_PERIODS = (30, 90, 180, 360)
LEGACY_PERIOD_FACTORS = {1: Decimal("1"), 6: Decimal("5.5"), 12: Decimal("10")}
CENT = Decimal("0.01")


class PricingError(ValueError):
    """价格不可用（缺月价、周期不支持）。路由层负责映射成 4xx。"""


def product_country_code(db: Session, product: Product) -> str:
    region = db.get(Region, product.region_id)
    if not region or not region.country_code:
        raise PricingError("商品缺少国家编码，无法按价目表报价")
    return region.country_code.strip().upper()


def monthly_price_for(db: Session, resource_category: str, country_code: str) -> Decimal:
    """按（资源分类, 国家）取月价。资源池下单走这条，不依赖 Product 实例。"""
    rule = db.scalar(select(RetailPriceRule).where(
        RetailPriceRule.resource_category == resource_category,
        RetailPriceRule.country_code == country_code.strip().upper(),
        RetailPriceRule.billing_days == BASE_PERIOD_DAYS,
    ))
    if not rule:
        raise PricingError("该产品分类与国家尚未配置月价，请先在零售价页补价")
    return Decimal(str(rule.unit_price)).quantize(CENT, rounding=ROUND_HALF_UP)


def monthly_price_of(db: Session, product: Product) -> Decimal:
    if not product.resource_category:
        raise PricingError("该商品未归入资源分类，无法按价目表报价")
    return monthly_price_for(db, product.resource_category, product_country_code(db, product))


def unit_price_for(base: Decimal, billing_days: int) -> Decimal:
    """线性派生：90 天 = 3×，180 天 = 6×，360 天 = 12×。"""
    if billing_days <= 0 or billing_days % BASE_PERIOD_DAYS:
        raise PricingError("使用周期必须是 30 天的整数倍")
    periods = Decimal(billing_days) / Decimal(BASE_PERIOD_DAYS)
    return (base * periods).quantize(CENT, rounding=ROUND_HALF_UP)


def sells_full_ladder(product: Product) -> bool:
    """只有基础周期商品（30 天）对外提供全部档位。

    供应商同步会按国家 × 分类 × 周期生成商品，180 天那个商品本身只代表 180 天，
    因此不能让它再挂出 30/360 天，否则同一个国家会出现重复档位。
    """
    return product.billing_days == BASE_PERIOD_DAYS


def periods_of(db: Session, product: Product) -> list[dict]:
    """该商品所有可售周期及单价；固定价商品只返回自身周期。"""
    if not product.resource_category:
        return [{
            "billing_days": product.billing_days,
            "unit_price": round(float(product.price), 2),
        }]
    try:
        base = monthly_price_of(db, product)
    except PricingError:
        return []                                  # 缺月价 → 前端置灰
    days_list = SELLABLE_PERIODS if sells_full_ladder(product) else (product.billing_days,)
    return [
        {"billing_days": days, "unit_price": round(float(unit_price_for(base, days)), 2)}
        for days in days_list
    ]


def quote(db: Session, product: Product, quantity: int, billing_days: int) -> tuple[Decimal, Decimal]:
    """返回 (该周期单价, 合计)。全站唯一取价函数。"""
    if quantity < 1:
        raise PricingError("数量必须大于 0")
    if product.resource_category:
        allowed = SELLABLE_PERIODS if sells_full_ladder(product) else (product.billing_days,)
        if billing_days not in allowed:
            raise PricingError(f"该商品仅支持 {'/'.join(map(str, allowed))} 天周期")
        unit = unit_price_for(monthly_price_of(db, product), billing_days)
    else:
        if product.billing_days <= 0 or billing_days % product.billing_days:
            raise PricingError("使用周期不合法")
        factor = LEGACY_PERIOD_FACTORS.get(billing_days // product.billing_days)
        if factor is None:
            raise PricingError("仅支持基础周期、6 个周期或 12 个周期")
        unit = (Decimal(str(product.price)) * factor).quantize(CENT, rounding=ROUND_HALF_UP)
    total = (unit * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
    return unit, total


def display_price_of(db: Session, product: Product) -> float:
    """商品自身周期的单价，用于 Product.price 展示缓存。"""
    if not product.resource_category:
        return round(float(product.price), 2)
    try:
        return round(float(unit_price_for(monthly_price_of(db, product), product.billing_days)), 2)
    except PricingError:
        return 0.0


def sync_display_price(db: Session, product: Product) -> bool:
    """把月价派生的自身周期价回写到 Product.price；返回是否发生变化。"""
    if not product.resource_category:
        return False
    new_price = display_price_of(db, product)
    if not new_price or abs(float(product.price) - new_price) < 0.005:
        return False
    product.price = new_price
    return True
