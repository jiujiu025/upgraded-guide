"""按零售价目表的月价回填商品定价数据（只影响展示价，不改历史订单）。

做三件事：
    1. 给 product_type 命中四个分类标签的商品补上 resource_category；
    2. 把 Product.price 刷新为「月价 × 该商品自身周期数」；
    3. 可选：把价目表里 90/180/360 天的旧打折行刷成线性值（休眠数据，不影响报价）。

用法：
    python scripts/backfill_product_pricing.py --dry-run
    python scripts/backfill_product_pricing.py --apply
    python scripts/backfill_product_pricing.py --apply --normalize-periods
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Product, Region, RetailPriceRule
from app.services.pricing_service import BASE_PERIOD_DAYS, PricingError, monthly_price_for, unit_price_for
from app.services.supplier_service import RESOURCE_CATEGORY_CODES


def _category_of(product_type: str) -> str | None:
    for code, label in RESOURCE_CATEGORY_CODES.items():
        if product_type == label:
            return code
    return None


def plan_categories(db) -> tuple[dict[int, str], list[str]]:
    """返回 {product_id: 分类代码} 与需要跳过的固定价商品类型。"""
    overrides, skipped = {}, []
    for product in db.scalars(select(Product).where(Product.resource_category.is_(None))):
        code = _category_of(product.product_type)
        if code:
            overrides[product.id] = code
        else:
            skipped.append(product.product_type)
    return overrides, sorted(set(skipped))


def plan_display_prices(db, overrides: dict[int, str]) -> list[tuple[Product, str, float, float]]:
    """返回需要更新的 [(商品, 分类, 现值, 应为值)]。固定价商品不在其中。"""
    region_codes = {region.id: (region.country_code or "").strip().upper()
                    for region in db.scalars(select(Region))}
    changes = []
    for product in db.scalars(select(Product)):
        code = product.resource_category or overrides.get(product.id)
        if not code:
            continue
        try:
            base = monthly_price_for(db, code, region_codes.get(product.region_id, ""))
            expected = float(unit_price_for(base, product.billing_days))
        except PricingError:
            continue
        if abs(float(product.price) - expected) < 0.005:
            continue
        changes.append((product, code, float(product.price), expected))
    return changes


def plan_period_normalization(db) -> list[tuple[RetailPriceRule, Decimal]]:
    """90/180/360 天旧打折价 → 线性值。这些行当前不参与报价。"""
    bases = {
        (rule.resource_category, rule.country_code.upper()): rule.unit_price
        for rule in db.scalars(select(RetailPriceRule).where(RetailPriceRule.billing_days == BASE_PERIOD_DAYS))
    }
    changes = []
    for rule in db.scalars(select(RetailPriceRule).where(RetailPriceRule.billing_days != BASE_PERIOD_DAYS)):
        base = bases.get((rule.resource_category, rule.country_code.upper()))
        if base is None:
            continue
        expected = (base * rule.billing_days / BASE_PERIOD_DAYS).quantize(Decimal("0.01"))
        if expected != rule.unit_price:
            changes.append((rule, expected))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="回填商品分类与展示价")
    parser.add_argument("--apply", action="store_true", help="真正写库；不加则只预览")
    parser.add_argument("--dry-run", action="store_true", help="只预览（默认行为，显式声明用）")
    parser.add_argument("--normalize-periods", action="store_true",
                        help="同时把价目表长周期旧价刷成线性值（休眠数据）")
    args = parser.parse_args()
    print(f"=== 定价数据回填（{'APPLY' if args.apply else 'DRY-RUN'}）===")

    db = SessionLocal()
    try:
        overrides, skipped = plan_categories(db)
        counts: dict[str, int] = {}
        for code in overrides.values():
            counts[code] = counts.get(code, 0) + 1
        print(f"[分类回填] 命中 {len(overrides)} 个商品：{counts or '无'}")
        if skipped:
            print(f"[分类回填] 跳过固定价商品类型（保持 NULL）：{skipped}")

        price_changes = plan_display_prices(db, overrides)
        print(f"[展示价刷新] {len(price_changes)} 个商品的展示价需要更新")
        for product, code, old, new in price_changes[:20]:
            print(f"    #{product.id} {product.name} ({code}, {product.billing_days}天): {old:g} → {new:g}")
        if len(price_changes) > 20:
            print(f"    ……另有 {len(price_changes) - 20} 条")

        period_changes = plan_period_normalization(db) if args.normalize_periods else []
        if args.normalize_periods:
            print(f"[长周期行归一] {len(period_changes)} 条价目行与线性值不一致（当前不参与报价）")
            for rule, expected in period_changes[:10]:
                print(f"    #{rule.id} {rule.resource_category}/{rule.country_code} {rule.billing_days}天: "
                      f"{rule.unit_price} → {expected}")
            if len(period_changes) > 10:
                print(f"    ……另有 {len(period_changes) - 10} 条")

        if not args.apply:
            print("DRY-RUN 未写库；确认无误后加 --apply 执行。")
            return 0

        # 1) 先落分类（含没有价格变化、但需要归类的商品）
        for product_id, code in overrides.items():
            product = db.get(Product, product_id)
            if product:
                product.resource_category = code
        # 2) 再落展示价
        for product, _code, _old, new in price_changes:
            product.price = new
        # 3) 可选：清理休眠的长周期行
        for rule, expected in period_changes:
            rule.unit_price = expected
        db.commit()
        print("已提交。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
