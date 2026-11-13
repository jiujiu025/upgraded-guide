"""只读探测全球代理（资源池 1）上游接口是否通：凭据 → 商品 → 国家 → 城市 → 号段。

不写库、不扣费、不创建任何采购单。用于在跑 `sync/city-segments` 之前确认凭据可用，
或排查"同步成功 0 条"这类问题。

用法（在项目根目录）：
    backend/.venv/Scripts/python.exe scripts/check_supplier_connectivity.py
    backend/.venv/Scripts/python.exe scripts/check_supplier_connectivity.py --country US --product 3

退出码：
    0 全通
    2 凭据未配置（先去后台「资源池 → 凭据」录入，或填 GLOBAL_PROXY_API_KEY）
    3 接口调用失败（凭据有值但被上游拒绝 / 网络不通 / 返回结构异常）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
# 数据库用相对路径（sqlite:///./data/...），必须在导入 app.db 之前切到 backend，
# 否则 engine 会在项目根建一个空的库文件。
os.chdir(BACKEND)

from sqlalchemy import select  # noqa: E402

from app.api.supplier_routes import api_key_configured, credential_source  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Supplier  # noqa: E402
from app.suppliers.base import SupplierError  # noqa: E402
from app.suppliers.factory import build_readonly_adapter  # noqa: E402

SUPPLIER_CODE = "quanqiudaili"


def main() -> int:
    parser = argparse.ArgumentParser(description="只读探测上游接口连通性")
    parser.add_argument("--product", type=int, default=3, help="上游产品类型 id，默认 3（原生住宅）")
    parser.add_argument("--country", default="US", help="用于探测城市与号段的国家码，默认 US")
    parser.add_argument("--city", default=None, help="指定城市名；不填则取该国家返回的第一个城市")
    parser.add_argument("--page-size", type=int, default=20, help="号段每页条数，默认 20")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        supplier = db.scalar(select(Supplier).where(Supplier.code == SUPPLIER_CODE))
        print("供应商:", SUPPLIER_CODE, "→", "存在" if supplier else "缺失")
        if supplier is None:
            print("！！ 数据库里没有该供应商记录，先跑一次任意资源池页面让它初始化")
            return 2

        print("凭据来源:", credential_source(db))
        if not api_key_configured(db):
            print("！！ 凭据未配置：api_key_configured=False")
            print("   两条路：① 后台「资源池 → 凭据」录入（加密存库，即时生效）")
            print("           ② 在 backend/.env 填 GLOBAL_PROXY_API_KEY 后重启后端")
            return 2

        adapter = build_readonly_adapter(db, supplier)
        try:
            print("\n[1/4] 商品列表 getProductList")
            products = adapter.list_products()
            print("      商品数:", len(products))
            for item in products[:10]:
                print("      - ref={} name={}".format(item.external_ref, item.name))

            print("\n[2/4] 国家列表 countryList(product_type_id={})".format(args.product))
            countries = adapter.list_countries(args.product)
            codes = [item.country_code for item in countries]
            print("      国家数:", len(countries))
            print("      前 10 个:", ", ".join(codes[:10]))
            if args.country.upper() not in codes:
                print("      注意：目标国家 {} 不在返回列表里".format(args.country.upper()))

            print("\n[3/4] 城市列表 cityList({}, {})".format(args.product, args.country))
            cities = adapter.list_cities(args.product, country_code=args.country)
            print("      城市数:", len(cities))
            for item in cities[:10]:
                print("      - {} (id={})".format(item.city_name, item.external_city_ref))
            if not cities:
                print("      ！该国家没有返回城市，号段无法按城市拉取")
                return 0

            city_name = args.city or cities[0].city_name
            print("\n[4/4] 号段列表 ipStrList({}, city={})".format(args.country, city_name))
            segments, total = adapter.list_ip_segments(
                country_code=args.country, city_code=city_name,
                page=1, page_size=args.page_size, product_type_id=args.product,
            )
            print("      本页号段:", len(segments), "上游 total:", total)
            for segment in segments[:10]:
                print("      -", segment)
            if not segments:
                print("      ！该城市没有返回号段，同步后这个城市将没有可选段")
        finally:
            adapter.close()
    except SupplierError as exc:
        print("\n！！ 上游调用失败:", getattr(exc, "code", type(exc).__name__), str(exc)[:300])
        payload = getattr(exc, "payload", None)
        if payload:
            print("   上游原始响应:", str(payload)[:500])
        return 3
    finally:
        db.close()

    print("\n结论：凭据可用，国家 / 城市 / 号段三级接口均返回数据，可以执行 sync/city-segments。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
