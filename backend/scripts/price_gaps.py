"""只读检查：三条官网产品线在各国家上的报价缺口。

用途：官网允许"有国家、无报价"（显示「暂无报价」且不可下单）。这个脚本把缺口
一次性列清楚，方便运营决定先补哪个国家，而不是逐个页面试。

不改任何数据，可随时跑：
    python scripts/price_gaps.py

只统计**当前在售的三条产品线**（静态住宅IP / 社媒运营专线 / 直播专线）。
库里的历史分类（机房、ISP 原生等）已不再对外售卖，其价则只做数量提示，
不逐条列出——否则几十行噪音会把真正的缺口淹掉。

输出三块：
1. 每条产品线缺价的国家（启用地区里有、该线没有 30 天价）
2. 属于三条线的报价，但国家清单里没有对应地区（报价闲置，官网看不到）
3. 属于三条线的报价，但国家没有可下单的商品（价则存在却买不了）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.data.site_catalog import (  # noqa: E402
    ACTIVE_CATEGORIES,
    CATEGORY_TO_LINE,
    LEGACY_CATEGORIES,
    LINE_LABELS,
    LINE_ORDER,
)
from app.db import SessionLocal  # noqa: E402
from app.models import Product, Region, RetailPriceRule  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        active = {r.country_code: r for r in db.scalars(
            select(Region).where(Region.is_active.is_(True)))}
        known_codes = {r.country_code for r in db.scalars(select(Region))}

        # 只看在售分类。历史分类（原生/ISP/机房）映射到住宅线只是为了让旧商品
        # 还能在后台被找到，它们没有在售商品，算进来会产生上百条假缺口。
        line_categories = {code: set() for code in LINE_ORDER}
        for category in ACTIVE_CATEGORIES:
            line_categories[CATEGORY_TO_LINE[category]].add(category)

        rules = list(db.scalars(
            select(RetailPriceRule).where(RetailPriceRule.billing_days == 30)))
        site_rules = [r for r in rules if r.resource_category in ACTIVE_CATEGORIES]
        legacy_rules = [r for r in rules if r.resource_category in LEGACY_CATEGORIES]

        usable = {
            (p.resource_category, p.region_id)
            for p in db.scalars(select(Product).where(Product.is_active.is_(True)))
        }

        print("=" * 68)
        print(f"1. 各产品线缺价的国家（启用地区 {len(active)} 个，只统计 30 天价）")
        print("=" * 68)
        total_gaps = 0
        for line in sorted(LINE_ORDER, key=lambda c: LINE_ORDER[c]):
            cats = line_categories[line]
            priced = {r.country_code for r in site_rules if r.resource_category in cats}
            covered = priced & set(active)
            missing = sorted(f"{r.name}({code})" for code, r in active.items() if code not in priced)
            total_gaps += len(missing)
            print(f"\n{LINE_LABELS[line]}：有价 {len(covered)}/{len(active)}")
            for item in missing:
                print(f"    缺  {item}")
            if not missing:
                print("    无缺口")

        print()
        print("=" * 68)
        print("2. 属于三条线的报价，但国家清单里没有对应地区（闲置，官网看不到）")
        print("=" * 68)
        orphan: dict[str, set[str]] = {}
        for rule in site_rules:
            if rule.country_code not in known_codes:
                orphan.setdefault(rule.country_code, set()).add(rule.resource_category)
        if orphan:
            for code in sorted(orphan):
                print(f"    {code}：{'、'.join(sorted(orphan[code]))}")
            print(f"    合计 {len(orphan)} 个国家。上架方法：把国家加进 "
                  f"app/data/site_catalog.py 的 COUNTRIES 后重跑种子脚本。")
        else:
            print("    无")

        print()
        print("=" * 68)
        print("3. 属于三条线的报价，但该国家没有可下单的商品")
        print("=" * 68)
        broken = []
        for rule in site_rules:
            region = active.get(rule.country_code)
            if region is None:
                continue
            if (rule.resource_category, region.id) not in usable:
                broken.append(f"{region.name}({rule.country_code}) "
                              f"{rule.resource_category} ¥{rule.unit_price}")
        if broken:
            for item in broken:
                print(f"    {item}")
        else:
            print("    无")

        print()
        print("=" * 68)
        print(f"汇总：缺价 {total_gaps} 处 | 闲置报价国家 {len(orphan)} 个 | 坏价则 {len(broken)} 条")
        if legacy_rules:
            legacy_cats = sorted({r.resource_category for r in legacy_rules})
            print(f"另：历史分类（{'、'.join(legacy_cats)}）有 {len(legacy_rules)} 条 30 天价则，")
            print("    这些分类已不在三条产品线内，不上官网，属预期闲置。")
        print("补价入口：后台「商品与价格 → 零售价」，或改 app/data/site_catalog.py 后重跑")
        print("           python scripts/seed_site_catalog.py --apply")
        print("=" * 68)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
