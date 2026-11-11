"""把上游城市与 IP 段同步成「客户可选的城市/号段」。

资源池 1（全球代理）的原生住宅接口能按国家 → 城市 → IP 段逐层查：
`cityList` 给城市，`ipStrList` 给该城市可售的段（前两段，如 149.52）。
这些段客户可以直接选，所以这里把它们写进 `catalog_city_options`。

命名规则（见 docs/双上游资源池设计.md）：
- 能匹配到我们已有城市码（中文名/三字码）的，**合并段**，不新建城市；
- 匹配不上的上游城市先落库为 `is_enabled=False` + 备注「待归属」，
  不直接出现在客户下拉里——城市命名以规范表为准，避免同一城市出现两个名字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogCityOption
from app.services.city_option_service import MAX_SEGMENTS, segments_text

SUPPLIER_SOURCE = "supplier_global_proxy"
CATEGORY = "static_residential"
# ip_segments 是 String(500)，按实际长度截断，别让同步把行写爆。
MAX_SEGMENT_TEXT_LENGTH = 480

# 上游城市名（小写）→ 我们已有的城市码。只覆盖已配置的城市，其余进"待归属"。
CITY_ALIASES: dict[str, str] = {
    "los angeles": "LAX", "new york": "NYC", "new york city": "NYC", "san jose": "SJC",
    "chicago": "CHI", "dallas": "DFW", "miami": "MIA",
    "toronto": "YYZ", "vancouver": "YVR", "montreal": "YUL",
    "london": "LON", "manchester": "MAN",
    "frankfurt": "FRA", "berlin": "BER",
    "paris": "PAR", "marseille": "MRS",
    "amsterdam": "AMS",
    "madrid": "MAD", "barcelona": "BCN",
    "milan": "MIL", "rome": "ROM",
    "tokyo": "TYO", "osaka": "OSA",
    "seoul": "SEL", "busan": "PUS",
    "hong kong": "HKG", "taipei": "TPE", "singapore": "SIN",
    "kuala lumpur": "KUL", "bangkok": "BKK",
    "ho chi minh city": "SGN", "hanoi": "HAN",
    "jakarta": "JKT", "manila": "MNL",
    "mumbai": "BOM", "delhi": "DEL", "new delhi": "DEL",
    "dubai": "DXB", "istanbul": "IST",
    "sao paulo": "SAO", "mexico city": "MEX",
    "sydney": "SYD", "melbourne": "MEL",
    "warsaw": "WAW", "moscow": "MOW", "tel aviv": "TLV",
    "riyadh": "RUH", "johannesburg": "JNB", "dhaka": "DAC",
    "karachi": "KHI", "sofia": "SOF", "budapest": "BUD", "copenhagen": "CPH",
}


@dataclass
class CitySegmentSyncResult:
    countries: int = 0
    cities_seen: int = 0
    matched: int = 0
    created_pending: int = 0
    segments_saved: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "countries": self.countries,
            "cities_seen": self.cities_seen,
            "matched": self.matched,
            "created_pending": self.created_pending,
            "segments_saved": self.segments_saved,
            "errors": self.errors[:20],
        }


def _fallback_code(name: str) -> str:
    """匹配不上的城市：用大写、去符号的短码占位（仅内部用，不对外展示）。"""
    code = re.sub(r"[^0-9A-Za-z]+", "", name).upper()
    return (code or "CITY")[:32]


def _merge_segments(existing: str, incoming: list[str]) -> tuple[str, int]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in f"{existing or ''},{','.join(incoming)}".split(","):
        value = raw.strip().upper()
        if not value or value in seen:
            continue
        if len(",".join(values + [value])) > MAX_SEGMENT_TEXT_LENGTH:
            break
        seen.add(value)
        values.append(value)
        if len(values) >= MAX_SEGMENTS:
            break
    return segments_text(values), len(values)


def sync_city_segments(
    db: Session,
    adapter,
    *,
    product_type_id: int = 3,
    country_codes: list[str] | None = None,
    segment_page_size: int = 40,
    max_pages: int = 1,
) -> CitySegmentSyncResult:
    """按国家拉城市与号段，合并进 catalog_city_options（幂等）。

    只同步 `product_type_id` 对应产品；默认原生 3。
    号段每城市最多取 `segment_page_size * max_pages` 个，避免把上游几千条全拉回来。
    """
    result = CitySegmentSyncResult()
    countries = adapter.list_countries(product_type_id)
    wanted = {code.upper() for code in country_codes} if country_codes else None
    for country in countries:
        code = country.country_code.upper()
        if wanted and code not in wanted:
            continue
        result.countries += 1
        try:
            cities = adapter.list_cities(product_type_id, country_code=code)
        except Exception as exc:  # 单个国家失败不影响其它国家
            result.errors.append(f"{code}: {type(exc).__name__}")
            continue
        for city in cities:
            name = (city.city_name or "").strip()
            if not name:
                continue
            result.cities_seen += 1
            segments: list[str] = []
            try:
                for page in range(1, max_pages + 1):
                    batch, _total = adapter.list_ip_segments(
                        country_code=code, city_code=name, page=page, page_size=segment_page_size,
                    )
                    segments.extend(batch)
                    if len(batch) < segment_page_size:
                        break
            except Exception as exc:
                result.errors.append(f"{code}/{name}: {type(exc).__name__}")
                continue

            canonical = CITY_ALIASES.get(name.lower())
            option = None
            if canonical:
                option = db.scalar(select(CatalogCityOption).where(
                    CatalogCityOption.resource_category == CATEGORY,
                    CatalogCityOption.country_code == code,
                    CatalogCityOption.city_code == canonical,
                ))
            if option is None:
                option = db.scalar(select(CatalogCityOption).where(
                    CatalogCityOption.resource_category == CATEGORY,
                    CatalogCityOption.country_code == code,
                    CatalogCityOption.city_name == name,
                ))
            if option is None:
                option = CatalogCityOption(
                    resource_category=CATEGORY,
                    country_code=code,
                    city_code=canonical or _fallback_code(name),
                    city_name=name,
                    ip_segments="",
                    # 匹配不上的先不开放给客户：城市命名以规范表为准
                    is_enabled=bool(canonical),
                    sort_order=500,
                    note=None if canonical else "待归属：上游城市名，确认后启用",
                    data_source=SUPPLIER_SOURCE,
                )
                db.add(option)
                db.flush()
                result.created_pending += 1
            else:
                result.matched += 1
                if option.data_source == "manual":
                    # 保留手工维护的启停与展示名，只把真实号段合并进来
                    option.data_source = SUPPLIER_SOURCE

            merged, count = _merge_segments(option.ip_segments, segments)
            if merged != option.ip_segments:
                option.ip_segments = merged
            result.segments_saved += count
        db.flush()
    return result


def sync_city_segments_985(
    db: Session,
    adapter,
    *,
    country_codes: list[str] | None = None,
    static_proxy_type: str = "premium",
    fetch_segments: bool = True,
    max_segment_cities: int = 20,
) -> "CitySegmentSyncResult":
    """资源池 2（985Proxy）：按库存把国家/城市写进客户可选城市清单。

    与资源池 1 的差别：
    - 985 的城市名是英文（`Los Angeles`），走同一张别名表映射到 LAX/NYC；
    - 号段要单独按 (国家, 城市) 拉，城市多时请求会成倍增加，所以只给前 N 个城市拉段，
      其余先只落城市、段留空（客户选到该城市时由线上实时查或后续补同步）。

    幂等：重复执行不会产生重复城市；匹配不上别名的一律停用待归属，不直接对客户开放。
    """
    result = CitySegmentSyncResult()
    try:
        rows = adapter.list_inventory(static_proxy_type=static_proxy_type)
    except Exception as exc:  # noqa: BLE001 - 上游异常统一进 errors，不中断同步
        result.errors.append(f"读取库存失败：{exc}")
        return result

    wanted = {code.upper() for code in country_codes} if country_codes else None
    segment_budget = max(0, max_segment_cities)
    seen: set[tuple[str, str]] = set()

    for row in rows:
        code = str(row.get("country") or "").upper()
        city_name = str(row.get("city") or "").strip()
        if not code or not city_name:
            continue
        if wanted and code not in wanted:
            continue
        key = (code, city_name.lower())
        if key in seen:
            continue
        seen.add(key)

        canonical = CITY_ALIASES.get(city_name.lower())
        city_code = canonical or _fallback_code(city_name)
        option = db.scalar(select(CatalogCityOption).where(
            CatalogCityOption.country_code == code,
            CatalogCityOption.city_code == city_code,
        ))
        segments: list[str] = []
        if fetch_segments and row.get("can_spec") and segment_budget > 0:
            try:
                segments = adapter.list_ip_segments(country=code, city=city_name,
                                                    static_proxy_type=static_proxy_type)
                segment_budget -= 1
            except Exception as exc:  # noqa: BLE001 - 单个城市失败不影响其它城市
                result.errors.append(f"{code}/{city_name} 号段读取失败：{exc}")

        if option is None:
            option = CatalogCityOption(
                resource_category=CATEGORY,
                country_code=code,
                city_code=city_code,
                city_name=city_name,
                ip_segments="",
                is_enabled=bool(canonical),
                sort_order=500,
                note=None if canonical else "待归属：985 城市名，确认后启用",
                data_source="supplier_985",
            )
            db.add(option)
            db.flush()
            result.created_pending += 1
        else:
            result.matched += 1
            if option.data_source == "manual":
                continue

        if segments:
            merged, saved = _merge_segments(option.ip_segments or "", segments)
            option.ip_segments = merged
            result.segments_saved += saved

    result.cities_seen = len(seen)
    return result
