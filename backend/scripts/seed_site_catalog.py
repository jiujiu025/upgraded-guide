"""把官网三类产品线的国家与价格写入数据库（幂等，可重复执行）。

    python scripts/seed_site_catalog.py            # 预演，只打印将要做的事
    python scripts/seed_site_catalog.py --apply    # 实际写入

做的事情：
1. 按国家清单补 Region（按名称幂等）；
2. 按报价表写 RetailPriceRule：30 天价 + 线性派生的 90/180/360 档；
3. 为每个「分类 × 国家」生成一个 30 天商品（其他周期由定价服务线性派生），
   商品可售（is_in_stock=True），因为这三类都是自有产品，不依赖供应商库存。

只新增/更新，不删除任何东西。后台「商品与价格」页可以继续逐条改价，
改了月价会自动回写商品展示价。
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.data.site_catalog import (
    CATEGORY_LABELS, COUNTRIES, LIVE_PRICES, LINE_LABELS, missing_prices, price_rows,
)
from app.db import SessionLocal
from app.models import Product, Region, RetailPriceRule

BASE_DAYS = 30
# 与 pricing_service 的线性口径保持一致：90 天 = 3×、180 天 = 6×、360 天 = 12×。
PERIOD_FACTORS = ((30, 1), (90, 3), (180, 6), (360, 12))
SOURCE_VERSION = "site-catalog-2026-11-09"


def run(apply: bool) -> int:
    db = SessionLocal()
    try:
        region_by_code: dict[str, Region] = {}
        created_regions = updated_regions = 0
        for code, name in COUNTRIES:
            region = db.scalar(select(Region).where(Region.name == name))
            if region is None:
                region = Region(name=name, country_code=code, is_active=True)
                db.add(region)
                db.flush()
                created_regions += 1
            else:
                if region.country_code != code or not region.is_active:
                    region.country_code = code
                    region.is_active = True
                    updated_regions += 1
            region_by_code[code] = region
        # 同一国家代码下如果还留着旧命名的地区（如"香港" vs "中国香港"），把它停用，
        # 否则官网会同时出现两个香港，其中一个没有任何报价。只停用，不动历史商品与线路。
        canonical_names = {code: name for code, name in COUNTRIES}
        deactivated = 0
        for region in db.scalars(select(Region)).all():
            expected = canonical_names.get(region.country_code)
            if expected and region.name != expected and region.is_active:
                region.is_active = False
                deactivated += 1
                print(f"停用重复地区：{region.name}（{region.country_code}），保留 {expected}")

        print(f"地区：新增 {created_regions}，修正 {updated_regions}，停用重复 {deactivated}，共 {len(region_by_code)} 个")

        rules_created = rules_updated = 0
        for category, country_code, price in price_rows():
            region = region_by_code.get(country_code)
            if region is None:
                print(f"跳过：未配置国家 {country_code}")
                continue
            for billing_days, factor in PERIOD_FACTORS:
                unit_price = Decimal(price * factor)
                rule = db.scalar(select(RetailPriceRule).where(
                    RetailPriceRule.resource_category == category,
                    RetailPriceRule.country_code == country_code,
                    RetailPriceRule.billing_days == billing_days,
                ))
                if rule is None:
                    db.add(RetailPriceRule(
                        resource_category=category, country_code=country_code,
                        billing_days=billing_days, unit_price=unit_price,
                        currency="CNY", source_version=SOURCE_VERSION,
                    ))
                    rules_created += 1
                elif rule.unit_price != unit_price:
                    rule.unit_price = unit_price
                    rule.source_version = SOURCE_VERSION
                    rules_updated += 1
        print(f"价格规则：新增 {rules_created}，更新 {rules_updated}")

        products_created = products_updated = 0
        for category, country_code, price in price_rows():
            region = region_by_code.get(country_code)
            if region is None:
                continue
            label = CATEGORY_LABELS[category]
            name = f"{region.name} {label} {BASE_DAYS}天"
            product = db.scalar(select(Product).where(Product.name == name))
            if product is None:
                db.add(Product(
                    name=name, product_type=label, price=float(price), billing_days=BASE_DAYS,
                    region_id=region.id, is_active=True, is_in_stock=True,
                    resource_category=category,
                ))
                products_created += 1
            else:
                changed = (
                    product.product_type != label
                    or product.resource_category != category
                    or float(product.price) != float(price)
                    or not product.is_active
                )
                if changed:
                    product.product_type = label
                    product.resource_category = category
                    product.price = float(price)
                    product.is_active = True
                    products_updated += 1
        print(f"商品：新增 {products_created}，更新 {products_updated}")

        gaps = missing_prices()
        for line, codes in gaps.items():
            if codes:
                print(f"缺报价（{LINE_LABELS[line]}）：{', '.join(codes)}")
            else:
                print(f"缺报价（{LINE_LABELS[line]}）：无")

        print()
        print("直播统一价：手机 ¥%d / 电脑 ¥%d（30 天）" % (LIVE_PRICES["mobile"], LIVE_PRICES["pc"]))
        if apply:
            db.commit()
            print("已写入数据库。")
        else:
            db.rollback()
            print("预演结束，未写入。加 --apply 执行。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="写入官网三类产品线的国家与价格")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只预演）")
    args = parser.parse_args()
    raise SystemExit(run(args.apply))
