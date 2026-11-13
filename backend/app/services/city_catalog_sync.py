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
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogCityOption
from app.services.city_option_service import MAX_SEGMENTS, POOL_ONE_SOURCE, segments_text

SUPPLIER_SOURCE = POOL_ONE_SOURCE
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
    # 2026-11-13 首次真实同步暴露的缺口：上游有这些城市且有号段，
    # 但白名单没有 → 全被写成「待归属」且不开放，客户看不到。
    "frankfurt am main": "FRA",
    "houston": "HOU",
    "san francisco": "SFO",
    "seattle": "SEA",
    "las vegas": "LAS",
    "phoenix": "PHX",
    "washington": "WAS",
    "boston": "BOS",
    "philadelphia": "PHL",
    "denver": "DEN",
    # 0 段但确是上游真实城市，先开城市（交付时按城市自动分配号段）
    "islamabad": "ISB",
    "sejong": "SJN",
    "cyberjaya": "CYB",
    "wroclaw": "WRW",
    # 故意不收录：Abingdon（英国小城，业务未确认）、Columbia（同名多州，有歧义），
    # 继续留在「待归属」等人工确认，避免客户选到语义不明的地点。
}


def city_key(name: str) -> str:
    """城市名归一化后再查别名表。

    上游会返回 ``São Paulo`` 这类带变音符号的名字，直接 lower() 永远匹配不上
    ``sao paulo``，于是同一城市会多出一行「待归属」、而原行永远没有号段。
    这里统一做 NFKD 分解 + 去组合符 + 折叠空白。
    """
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(folded.lower().split())


@dataclass
class CitySegmentSyncResult:
    countries: int = 0
    cities_seen: int = 0
    matched: int = 0
    created_pending: int = 0
    claimed: int = 0
    segments_saved: int = 0
    disabled_stale: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "countries": self.countries,
            "cities_seen": self.cities_seen,
            "matched": self.matched,
            "created_pending": self.created_pending,
            "claimed": self.claimed,
            "segments_saved": self.segments_saved,
            "disabled_stale": self.disabled_stale,
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
        seen_codes: set[str] = set()
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
                # 号段拉取失败不等于城市不可买：城市仍可由上游按城市分配，
                # 这里保留城市、只记错误，避免把“暂时查不到段”误判成“已下架”。
                result.errors.append(f"{code}/{name}: {type(exc).__name__}")

            canonical = CITY_ALIASES.get(city_key(name))
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
                # 别名补齐后要把历史上按名字兜底建的「待归属」行认领成规范城市码并开放，
                # 否则补了别名只是把号段写进一行仍然关闭的记录里，客户依旧看不到。
                # 只有当规范码还没被别的行占用时才改（法兰克福 FRA 已存在，走上面的精确匹配）。
                if canonical and option.city_code != canonical and not option.is_enabled:
                    taken = db.scalar(select(CatalogCityOption.id).where(
                        CatalogCityOption.resource_category == CATEGORY,
                        CatalogCityOption.country_code == code,
                        CatalogCityOption.city_code == canonical,
                    ))
                    if taken is None:
                        option.city_code = canonical
                        option.is_enabled = True
                        option.note = None
                        result.claimed += 1

            merged, count = _merge_segments(option.ip_segments, segments)
            if merged != option.ip_segments:
                option.ip_segments = merged
            result.segments_saved += count
            seen_codes.add(option.city_code.upper())

        # 上游本次没返回的城市 = 已下架。只处理池 1 自己写的行：
        # 手工清单与 985 的行不归这里管，不能顺手改掉。
        stale_rows = db.scalars(select(CatalogCityOption).where(
            CatalogCityOption.resource_category == CATEGORY,
            CatalogCityOption.country_code == code,
            CatalogCityOption.data_source == SUPPLIER_SOURCE,
        )).all()
        for row in stale_rows:
            if row.city_code.upper() not in seen_codes and row.is_enabled:
                row.is_enabled = False
                row.note = "上游本次同步未返回该城市，已自动下架；确认恢复后手动启用"
                result.disabled_stale += 1
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
                # 985 目前只有只读接口，采购链路走的是池 1。这里同步进来的城市与号段
                # 只能当预研数据：一旦对客户开放，客户就会选到一个"下单必然失败"的段。
                # 等 985 下单接入并跑通沙箱后，由运营在后台显式启用。
                is_enabled=False,
                sort_order=500,
                note=("985 城市：采购链路未接入，暂不开放" if canonical
                      else "待归属：985 城市名，确认后启用"),
                data_source="supplier_985",
            )
            db.add(option)
            db.flush()
            result.created_pending += 1
        else:
            result.matched += 1
            if option.data_source == "manual":
                continue
            if option.data_source != "supplier_985":
                # 这一行属于别的池（池 1 / 手工）。两个池的段不能混进同一行：
                # 客户按这一行选段，采购却按池 1 下单，买不到就会变成退款。
                # 双池并存的正确做法是按池分行存，见
                # docs/城市号段来源与多池归属方案.md。
                continue
            if option.is_enabled:
                # 早期版本把匹配上别名的 985 城市直接开了；现在纠正回来。
                option.is_enabled = False
                option.note = option.note or "985 城市：采购链路未接入，暂不开放"

        if segments:
            merged, saved = _merge_segments(option.ip_segments or "", segments)
            option.ip_segments = merged
            result.segments_saved += saved

    result.cities_seen = len(seen)
    return result
