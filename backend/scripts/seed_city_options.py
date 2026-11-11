"""把客户可选的「城市码 + IP 段」写入数据库（幂等，可重复执行）。

    python scripts/seed_city_options.py            # 预演，只打印将要做的事
    python scripts/seed_city_options.py --apply    # 实际写入

为什么需要这个脚本：城市清单原本要等上游库存同步才会有数据（supplier_regions 的
城市行），但本地/验收环境没有上游凭据，客户选城市的功能就会一直空着。
这里按业务常识先给在售国家各配一份城市清单（``data_source=manual``），
接上游后由同步任务覆盖（写 ``data_source=supplier_985``，后台仍可改）。

号段（IP 段）只给美国两个城市放示例值：号段是消耗品，编造出来的段会误导客户，
真实可售号段必须来自上游 ``spec_ip_segment_list``。示例段在备注里写清了来源。

只新增，不覆盖后台已改过的行；已存在的 (分类, 国家, 城市码) 直接跳过。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CatalogCityOption
from app.services.city_option_service import normalize_segments, segments_text

CATEGORY = "static_residential"

# 国家代码 → [(城市码, 城市名, 号段列表, 备注)]
CITIES: dict[str, list[tuple[str, str, list[str], str | None]]] = {
    "US": [
        ("LAX", "洛杉矶", ["23.94.5", "23.94.6", "23.94.7", "104.28.11", "104.28.12"], "示例号段，接上游后由库存同步覆盖"),
        ("NYC", "纽约", [], None),
        ("SJC", "圣何塞", [], None),
        ("CHI", "芝加哥", [], None),
        ("DFW", "达拉斯", [], None),
        ("MIA", "迈阿密", [], None),
    ],
    "CA": [("YYZ", "多伦多", [], None), ("YVR", "温哥华", [], None), ("YUL", "蒙特利尔", [], None)],
    "GB": [("LON", "伦敦", [], None), ("MAN", "曼彻斯特", [], None)],
    "DE": [("FRA", "法兰克福", [], None), ("BER", "柏林", [], None)],
    "FR": [("PAR", "巴黎", [], None), ("MRS", "马赛", [], None)],
    "NL": [("AMS", "阿姆斯特丹", [], None)],
    "ES": [("MAD", "马德里", [], None), ("BCN", "巴塞罗那", [], None)],
    "IT": [("MIL", "米兰", [], None), ("ROM", "罗马", [], None)],
    "JP": [("TYO", "东京", [], None), ("OSA", "大阪", [], None)],
    "KR": [("SEL", "首尔", [], None), ("PUS", "釜山", [], None)],
    "HK": [("HKG", "香港", [], None)],
    "TW": [("TPE", "台北", [], None)],
    "SG": [("SIN", "新加坡", [], None)],
    "MY": [("KUL", "吉隆坡", [], None)],
    "TH": [("BKK", "曼谷", [], None)],
    "VN": [("SGN", "胡志明市", [], None), ("HAN", "河内", [], None)],
    "ID": [("JKT", "雅加达", [], None)],
    "PH": [("MNL", "马尼拉", [], None)],
    "IN": [("BOM", "孟买", [], None), ("DEL", "德里", [], None)],
    "AE": [("DXB", "迪拜", [], None)],
    "TR": [("IST", "伊斯坦布尔", [], None)],
    "BR": [("SAO", "圣保罗", [], None)],
    "MX": [("MEX", "墨西哥城", [], None)],
    "AU": [("SYD", "悉尼", [], None), ("MEL", "墨尔本", [], None)],
    "PL": [("WAW", "华沙", [], None)],
    "RU": [("MOW", "莫斯科", [], None)],
    "IL": [("TLV", "特拉维夫", [], None)],
    "SA": [("RUH", "利雅得", [], None)],
    "ZA": [("JNB", "约翰内斯堡", [], None)],
    "BD": [("DAC", "达卡", [], None)],
    "PK": [("KHI", "卡拉奇", [], None)],
    "BG": [("SOF", "索菲亚", [], None)],
    "HU": [("BUD", "布达佩斯", [], None)],
    "DK": [("CPH", "哥本哈根", [], None)],
}


def run(apply: bool) -> int:
    db = SessionLocal()
    try:
        created = skipped = 0
        for country_code, cities in CITIES.items():
            for index, (city_code, city_name, segments, note) in enumerate(cities):
                exists = db.scalar(select(CatalogCityOption).where(
                    CatalogCityOption.resource_category == CATEGORY,
                    CatalogCityOption.country_code == country_code,
                    CatalogCityOption.city_code == city_code,
                ))
                if exists is not None:
                    skipped += 1
                    continue
                created += 1
                print(f"+ {country_code} {city_code} {city_name} 号段={segments or '无'}")
                if apply:
                    db.add(CatalogCityOption(
                        resource_category=CATEGORY,
                        country_code=country_code,
                        city_code=city_code,
                        city_name=city_name,
                        ip_segments=segments_text(normalize_segments(",".join(segments))),
                        is_enabled=True,
                        sort_order=(index + 1) * 10,
                        note=note,
                        data_source="manual",
                    ))
        if apply:
            db.commit()
        print(f"\n新建 {created} 个城市，跳过已存在 {skipped} 个；{'已写入' if apply else '预演，未写入'}")
        if not apply and created:
            print("加 --apply 实际写入")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="灌入示例城市与号段（幂等）")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库")
    raise SystemExit(run(parser.parse_args().apply))
