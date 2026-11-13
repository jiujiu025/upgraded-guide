"""中转层：社媒 / 直播在住宅 IP 之上多加一跳 VPS。

业务口径（2026-11-13 玖夜确认）：

- 静态住宅 IP：上游买一个住宅 IP，直出，无中转；
- 社媒运营专线：同一个住宅 IP + 普通 VPS 中转；
- 直播专线：同一个住宅 IP + 性能更好的 VPS 中转。

所以 **采购永远是同一件事**（买上游住宅 IP），三类产品的差别只在交付前那层中转。
这也是为什么 ``procurement_service`` 现在把三类都算作可采购——但社媒/直播在
中转配好之前绝不能被当成"已交付"，否则客户买到的是裸 IP。

中转的具体实现（3x-ui 面板 + API 建 inbound）的细节用户要求先跳过，这里只固定契约：

- ``requires_transit(category)``：这条产品线要不要中转；
- ``provision_transit(...)``：给已买到的住宅 IP 建中转，返回客户侧接入信息；
- 未接入时抛 ``TransitNotConfigured``，调用方必须把订单停在 ``pending_transit``，
  不写 ``CustomerLine``、不发"已开通"通知。

等 3x-ui 的接口契约确定后，只需要实现 ``provision_transit``，其余流程不用动。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

# 需要中转的产品线。静态住宅不在此列（直出）。
TRANSIT_CATEGORIES = {"social_media", "live_stream_mobile", "live_stream_pc"}


class TransitNotConfigured(Exception):
    """中转层未接入，或该城市没有可用 VPS。

    调用方应把订单置为 ``pending_transit``：IP 已经买了（钱已花），
    但还没加工完，不能交付。
    """


@dataclass(frozen=True)
class TransitEndpoint:
    """客户侧实际连的入口（中转 VPS），以及它背后的住宅出口 IP。"""

    server: str
    port: int
    protocol: str
    username: str | None
    password: str | None
    exit_ip: str
    panel_ref: str | None = None


def requires_transit(resource_category: str | None) -> bool:
    return (resource_category or "") in TRANSIT_CATEGORIES


def provision_transit(
    db: Session,
    *,
    order,
    accounts: list,
    country_code: str,
    city_name: str | None = None,
) -> list[TransitEndpoint]:
    """给已经买到的住宅 IP 逐个建立中转，返回客户侧接入信息。

    ``accounts`` 是上游返回的代理账号列表（带 ip / port / username / password）。

    当前未实现：3x-ui 面板地址、鉴权方式、inbound 模板都还没定。
    这里必须显式抛错，不能返回"原样直出"——那等于把裸 IP 卖成专线。
    """
    raise TransitNotConfigured(
        "中转层未接入：住宅 IP 已采购，等待配置中转后再交付（3x-ui 契约待定）"
    )
