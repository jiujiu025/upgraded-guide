"""客户可选城市码 / IP 段（号段）。

背景与设计取舍见 docs/城市与IP段选择设计.md：

- 官网目录只到国家，"城市"是内部参数；但客户（尤其做 TikTok / 支付 / 电商的）
  经常指定"要洛杉矶的 ISP 段"。所以把城市与号段做成**可选的精细化选项**：
  不选 = 自动分配，选了就必须按选择交付，不允许静默替换。
- 选项按 (resource_category, country_code) 维护，不按商品维护：同一国家的多个
  SKU 共用一份城市清单，和上游「国家 + 城市」的库存口径一致。
- 数据来源目前是后台人工维护（``data_source=manual``）；接 985proxy 后由库存与
  号段快照同步写入，接口形状不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogCityOption, Product, Region

AUTO_ASSIGN_LABEL = "自动分配（不指定城市）"
# 采购只走资源池 1。城市清单里只有这个来源的行能保证“能选就能买”；
# manual 是历史手工清单，985 目前只有只读接口。见 docs/城市号段来源与多池归属方案.md。
POOL_ONE_SOURCE = "supplier_global_proxy"
SEGMENT_SPLIT_PATTERN = re.compile(r"[,，;；\s]+")
# 上游号段形如 ``1.1.1``（/24 的前三段），也允许 ``1.1.1.0/24`` 这类完整写法。
SEGMENT_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-/]{1,30}$")
MAX_SEGMENTS = 60
CITY_CODE_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_\-]{0,31}$")


class CitySelectionError(Exception):
    """客户选的城市 / 号段不可售。message 直接回给前端展示。"""


@dataclass(frozen=True)
class CityChoice:
    city_code: str | None = None
    city_name: str | None = None
    ip_segment: str | None = None

    @property
    def specified(self) -> bool:
        return bool(self.city_code or self.ip_segment)


def normalize_segments(raw: str | None) -> list[str]:
    """把 ``1.1.1, 1.1.2`` 这类文本拆成去重后的号段列表（保持输入顺序）。"""
    if not raw:
        return []
    seen: list[str] = []
    upper = {item.upper() for item in seen}
    for chunk in SEGMENT_SPLIT_PATTERN.split(raw.strip()):
        value = chunk.strip().upper()
        if not value or value in upper:
            continue
        if not SEGMENT_PATTERN.match(value):
            raise ValueError(f"IP 段格式不合法：{chunk.strip()}")
        seen.append(value)
        upper.add(value)
        if len(seen) > MAX_SEGMENTS:
            raise ValueError(f"单个城市最多维护 {MAX_SEGMENTS} 个 IP 段")
    return seen


def segments_text(segments: list[str]) -> str:
    return ",".join(segments)


def normalize_city_code(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if not value:
        return ""
    if not CITY_CODE_PATTERN.match(value):
        raise ValueError("城市码只能包含字母、数字、下划线和短横线，且不超过 32 位")
    return value


def options_of(db: Session, resource_category: str | None, country_code: str | None,
               enabled_only: bool = True) -> list[CatalogCityOption]:
    if not resource_category or not country_code:
        return []
    query = select(CatalogCityOption).where(
        CatalogCityOption.resource_category == resource_category,
        CatalogCityOption.country_code == country_code.upper(),
    )
    if enabled_only:
        query = query.where(CatalogCityOption.is_enabled.is_(True))
    return list(db.scalars(query.order_by(CatalogCityOption.sort_order, CatalogCityOption.city_code)).all())


def sellable_options_of(db: Session, resource_category: str | None, country_code: str | None) -> list[CatalogCityOption]:
    """客户真正能选的城市：有池 1 同步数据时，只信池 1。

    为什么不是简单按 ``is_enabled`` 过滤：手工清单可以被人为打开，985 的城市也曾经
    被误开过；一旦池 1 有该国家的真实同步结果，就说明我们掌握了“上游实际卖什么”，
    此时再让手工/别池的行混进来，客户就会选到一个采购必然失败的段。

    没有池 1 数据的国家退回已启用清单（历史手工维护的国家仍可用），
    这是兼容路径而不是保证：管理后台会明确标出哪些行还没被池 1 核对过。
    """
    rows = options_of(db, resource_category, country_code, enabled_only=True)
    trusted = [row for row in rows if row.data_source == POOL_ONE_SOURCE]
    return trusted or rows


def option_view(option: CatalogCityOption) -> dict:
    return {
        "id": option.id,
        "resource_category": option.resource_category,
        "country_code": option.country_code,
        "city_code": option.city_code,
        "city_name": option.city_name,
        "segments": normalize_segments(option.ip_segments),
        "is_enabled": option.is_enabled,
        "sort_order": option.sort_order,
        "note": option.note,
        "data_source": option.data_source,
        "updated_at": option.updated_at,
    }


def customer_options(db: Session, product: Product) -> dict:
    """客户下单页用的城市清单。没配城市时 supported=False，前端只显示"自动分配"。"""
    region = db.get(Region, product.region_id) if product.region_id else None
    country_code = (region.country_code or "").upper() if region else ""
    items = sellable_options_of(db, product.resource_category, country_code) if region else []
    return {
        "supported": bool(items),
        "resource_category": product.resource_category,
        "country_code": country_code,
        "country_name": region.name if region else None,
        "auto_label": AUTO_ASSIGN_LABEL,
        "items": [
            {
                "city_code": option.city_code,
                "city_name": option.city_name,
                "segments": normalize_segments(option.ip_segments),
                "note": option.note,
            }
            for option in items
        ],
    }


def _match_option(options: list[CatalogCityOption], raw: str) -> CatalogCityOption | None:
    """城市码优先，其次允许用城市名（客户从截图里抄一个城市名也能下单）。"""
    key = raw.strip().upper()
    for option in options:
        if option.city_code.upper() == key:
            return option
    for option in options:
        if option.city_name.strip().upper() == key:
            return option
    return None


def resolve_choice(db: Session, product: Product, region: Region,
                   city_code: str | None, ip_segment: str | None) -> CityChoice:
    """校验并归一化客户选择，返回要写进订单的快照。

    严格失败而不是"自动换个城市"：客户指定了段就要拿到那个段，
    静默替换等于交付了客户没要的东西，后面必然退款。
    """
    code_raw = (city_code or "").strip()
    segment_raw = (ip_segment or "").strip()
    if not code_raw and not segment_raw:
        return CityChoice()

    country_code = (region.country_code or "").upper()
    options = sellable_options_of(db, product.resource_category, country_code)
    if not options:
        raise CitySelectionError("该地区暂不支持指定城市，请选择自动分配")

    option = _match_option(options, code_raw) if code_raw else None
    if code_raw and option is None:
        raise CitySelectionError(f"城市「{code_raw}」暂不可售，请重新选择或改为自动分配")

    if segment_raw:
        segment = segment_raw.upper()
        if not SEGMENT_PATTERN.match(segment):
            raise CitySelectionError("IP 段格式不正确")
        if option is not None:
            if segment not in {item.upper() for item in normalize_segments(option.ip_segments)}:
                raise CitySelectionError(f"IP 段 {segment} 不属于城市「{option.city_name}」，请重新选择")
        else:
            matches = [
                item for item in options
                if segment in {value.upper() for value in normalize_segments(item.ip_segments)}
            ]
            if not matches:
                raise CitySelectionError(f"IP 段 {segment} 暂不可售，请重新选择或改为自动分配")
            if len(matches) > 1:
                raise CitySelectionError(f"IP 段 {segment} 对应多个城市，请先选择城市")
            option = matches[0]
        return CityChoice(city_code=option.city_code, city_name=option.city_name, ip_segment=segment)

    return CityChoice(city_code=option.city_code, city_name=option.city_name)
