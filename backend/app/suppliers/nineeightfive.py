"""985Proxy 适配器（资源池 2）。

与全球代理的区别（见 docs/985proxy对接设计.md）：

- 鉴权是请求头 `apikey`，`code=0` 才算成功（全球代理是 `code=1`）；
- 购买是**异步**的：buy 只回订单号，IP 要 poll `order_result`；
- 城市和号段都是必填参数，不像全球代理那样可以只给国家就下单。

这里先实现「同步目录 + 查余额」这三件事（inventory / spec_ip_segment_list /
wallet balance）——它们不需要 zone，配好 Key 就能看到上游到底有哪些国家城市。
下单链路（buy → order_pay → order_result）等有 zone 和沙箱账号后再接，
现在写了也验证不了。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.suppliers.base import (
    SupplierBusinessError, SupplierConfigurationError, SupplierInvalidResponseError,
    SupplierRateLimitError, SupplierTimeoutError, SupplierUnavailableError,
)

logger = logging.getLogger(__name__)

INVENTORY_ENDPOINT = "/res_static/inventory"
SEGMENT_ENDPOINT = "/res_static/spec_ip_segment_list"
BALANCE_ENDPOINT = "/wallet/balance"

# 上游只有两档：premium = 原生（我们卖的），shared = 广播
PROXY_TYPES = {"premium": "premium", "shared": "shared"}


class NineEightFiveAdapter:
    """资源池 2 的读写适配器。目前只覆盖只读能力。"""

    def __init__(self, api_key: str | None, base_url: str, timeout_seconds: float = 15.0) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://open-api.985proxy.com").rstrip("/")
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------ 基础
    def _request(self, path: str, payload: dict | None = None, *, method: str = "POST") -> Any:
        if not self.api_key:
            raise SupplierConfigurationError("985Proxy 的 API Key 未配置")
        url = f"{self.base_url}{path}"
        headers = {"apikey": self.api_key, "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                if method == "GET":
                    response = client.get(url, headers=headers)
                else:
                    response = client.post(url, headers=headers, json=payload or {})
        except httpx.TimeoutException as exc:
            raise SupplierTimeoutError("985Proxy 请求超时") from exc
        except httpx.HTTPError as exc:
            raise SupplierUnavailableError(f"985Proxy 网络异常：{exc}") from exc

        if response.status_code == 429:
            raise SupplierRateLimitError("985Proxy 触发限流")
        if response.status_code >= 500:
            raise SupplierUnavailableError(f"985Proxy 服务异常（{response.status_code}）")
        try:
            body = response.json()
        except ValueError as exc:
            raise SupplierInvalidResponseError("985Proxy 返回的不是 JSON") from exc
        if not isinstance(body, dict):
            raise SupplierInvalidResponseError("985Proxy 返回结构无效")
        # code=0 成功；其他都当业务失败，把 msg 带回去
        if body.get("code") not in (0, "0", None):
            message = str(body.get("msg") or body.get("message") or "上游返回失败")
            raise SupplierBusinessError(f"985Proxy 业务错误：{message}")
        return body.get("data")

    # ------------------------------------------------------------------ 只读
    def list_inventory(self, *, static_proxy_type: str = "premium",
                       purpose_web: str | None = None) -> list[dict]:
        """把上游库存整理成 [{country, country_name, city, stock, price, can_spec}]。

        上游可能按国家分组返回，也可能是平铺列表；两种都兼容——字段名有多个版本，
        取不到就当没有，不猜。
        """
        payload: dict[str, Any] = {"static_proxy_type": PROXY_TYPES.get(static_proxy_type, "premium")}
        if purpose_web:
            payload["purpose_web"] = purpose_web
        data = self._request(INVENTORY_ENDPOINT, payload)
        return _flatten_inventory(data)

    def list_ip_segments(self, *, country: str, city: str,
                         static_proxy_type: str = "premium") -> list[str]:
        payload = {
            "static_proxy_type": PROXY_TYPES.get(static_proxy_type, "premium"),
            "country": country,
            "city": city,
        }
        data = self._request(SEGMENT_ENDPOINT, payload)
        if isinstance(data, list):
            return [str(item) for item in data if isinstance(item, (str, int))]
        return []

    def wallet_balance(self) -> dict:
        data = self._request(BALANCE_ENDPOINT, method="GET")
        return data if isinstance(data, dict) else {}


def _pick(row: dict, *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _flatten_inventory(data: Any) -> list[dict]:
    """兼容「按国家分组的嵌套结构」和「平铺列表」两种返回。"""
    rows: list[dict] = []

    def add(row: dict, country: str | None = None) -> None:
        code = _pick(row, "country", "country_code", "countryCode") or country
        city = _pick(row, "city", "city_name", "cityName")
        if not code:
            return
        try:
            stock = int(_pick(row, "stock", "inventory", "available") or 0)
        except (TypeError, ValueError):
            stock = 0
        price = _pick(row, "price", "unit_price", "sale_price")
        can_spec = _pick(row, "can_spec", "canSpec", "support_spec")
        rows.append({
            "country": str(code).upper(),
            "country_name": str(_pick(row, "country_name", "countryName") or ""),
            "city": str(city) if city else "",
            "stock": stock,
            "price": price,
            "can_spec": bool(can_spec) if can_spec is not None else False,
        })

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                add(item)
    elif isinstance(data, dict):
        # {"US": {"cities": [...]}} / {"US": [...]} / {"list": [...]}
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        add(item, country=key)
            elif isinstance(value, dict):
                cities = value.get("cities") or value.get("city_list") or []
                if isinstance(cities, list):
                    for item in cities:
                        if isinstance(item, dict):
                            add(item, country=key)
    logger.info("985 inventory 解析出 %s 条", len(rows))
    return rows
