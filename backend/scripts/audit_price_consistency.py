"""定价一致性巡检（只读）：确保"一个月只有一个价"。

检查三项：
    1. 归入资源分类的商品，是否有对应（分类, 国家）的 30 天月价；
    2. 商品展示价 Product.price 是否等于「月价 × 该商品自身周期数」；
    3. 价目表里 90/180/360 天的行是否仍是旧的打折价（休眠数据，仅提示）。

退出码：0 = 全部一致；1 = 存在第 1、2 类问题（可用于 CI）。

用法：
    python scripts/audit_price_consistency.py
    python scripts/audit_price_consistency.py --csv price_audit.csv
"""
import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Product, Region, RetailPriceRule
from app.services.pricing_service import BASE_PERIOD_DAYS, display_price_of


def main() -> int:
    parser = argparse.ArgumentParser(description="定价一致性巡检")
    parser.add_argument("--csv", help="把明细写到 CSV")
    args = parser.parse_args()

    db = SessionLocal()
    rows: list[dict] = []
    try:
        region_codes = {region.id: (region.country_code or "").upper()
                        for region in db.scalars(select(Region))}
        bases = {
            (rule.resource_category, rule.country_code.upper()): rule.unit_price
            for rule in db.scalars(select(RetailPriceRule).where(
                RetailPriceRule.billing_days == BASE_PERIOD_DAYS))
        }
        products = db.scalars(select(Product).where(Product.resource_category.is_not(None))).all()
        missing_price, price_mismatch = [], []
        for product in products:
            key = (product.resource_category, region_codes.get(product.region_id, ""))
            if key not in bases:
                missing_price.append((product, key))
                rows.append({"problem": "缺月价", "product_id": product.id, "name": product.name,
                             "category": product.resource_category, "country": key[1],
                             "billing_days": product.billing_days,
                             "current_price": product.price, "expected_price": ""})
                continue
            expected = display_price_of(db, product)
            if expected and abs(float(product.price) - expected) >= 0.005:
                price_mismatch.append((product, expected))
                rows.append({"problem": "展示价不一致", "product_id": product.id, "name": product.name,
                             "category": product.resource_category, "country": key[1],
                             "billing_days": product.billing_days,
                             "current_price": product.price, "expected_price": expected})

        dormant = []
        for rule in db.scalars(select(RetailPriceRule).where(RetailPriceRule.billing_days != BASE_PERIOD_DAYS)):
            base = bases.get((rule.resource_category, rule.country_code.upper()))
            if base is None:
                continue
            expected = (base * rule.billing_days / BASE_PERIOD_DAYS).quantize(Decimal("0.01"))
            if expected != rule.unit_price:
                dormant.append((rule, expected))
                rows.append({"problem": "长周期行非线性(不休眠不参与报价)", "product_id": "",
                             "name": f"{rule.resource_category}/{rule.country_code}",
                             "category": rule.resource_category, "country": rule.country_code,
                             "billing_days": rule.billing_days,
                             "current_price": str(rule.unit_price), "expected_price": str(expected)})

        print(f"归入资源分类的商品：{len(products)} 个")
        print(f"  缺月价：{len(missing_price)} 个")
        for product, key in missing_price[:10]:
            print(f"    #{product.id} {product.name} → 需要 {key[0]}/{key[1]} 的 30 天价")
        print(f"  展示价与派生价不一致：{len(price_mismatch)} 个")
        for product, expected in price_mismatch[:10]:
            print(f"    #{product.id} {product.name}: {product.price:g} → 应为 {expected:g}")
        print(f"价目表长周期行非线性：{len(dormant)} 条（当前不参与报价，可用 backfill --normalize-periods 清理）")

        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else
                                        ["problem", "product_id", "name", "category", "country",
                                         "billing_days", "current_price", "expected_price"])
                writer.writeheader()
                writer.writerows(rows)
            print(f"明细已写入 {args.csv}")

        blocking = len(missing_price) + len(price_mismatch)
        print(f"结论：{'不一致 ' + str(blocking) + ' 项' if blocking else '全部一致'}")
        return 1 if blocking else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
