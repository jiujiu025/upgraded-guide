import logging
import re
import time
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from app.suppliers.base import (
    InventoryCheckResult, InventoryItem, QuoteItem, SupplierBusinessError, SupplierCapabilityUnsupportedError,
    SupplierConfigurationError, SupplierInvalidResponseError, SupplierProductItem, SupplierRateLimitError,
    SupplierRegionItem, SupplierTimeoutError, SupplierUnavailableError,
)

logger = logging.getLogger(__name__)

PRODUCTS_ENDPOINT = "/externalapi/conpons/getProductList"
COUNTRIES_ENDPOINT = "/externalapi/inventory/countryList"
CITIES_ENDPOINT = "/externalapi/inventory/cityList"
INVENTORY_ENDPOINT = "/externalapi/inventory/getBuyProductSku"
INVENTORY_CHECK_ENDPOINT = "/externalapi/product_order/checkInventory"
QUOTES_ENDPOINT = "/externalapi/product_price/getProductUnitPrice"
SUPPORTED_REGION_PRODUCT_ID = 2


def response_shape(value: Any, depth: int = 0) -> dict:
    if depth >= 4:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        fields = {}
        for key, item in list(value.items())[:30]:
            name = key if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) else "<dynamic-key>"
            fields[name] = response_shape(item, depth + 1)
        return {"type": "object", "fields": fields, "truncated": len(value) > 30}
    if isinstance(value, list):
        return {"type": "array", "length": len(value), "item": response_shape(value[0], depth + 1) if value else None}
    return {"type": type(value).__name__}


class GlobalProxyReadOnlyAdapter:
    """全球代理只读适配器，不包含购买、续费、退单或凭据查询能力。"""

    def __init__(
        self,
        api_key: str | None,
        base_url: str = "https://admin.quanqiudaili.com",
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.25,
        max_retry_delay_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("供应商只读重试次数必须在 1 到 5 之间")
        if backoff_base_seconds < 0 or max_retry_delay_seconds <= 0:
            raise ValueError("供应商重试退避参数无效")
        self._api_key = api_key
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._sleep = sleep
        self.last_quote_response_shape: dict | None = None
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    delay = retry_at.timestamp() - time.time()
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            if delay > 0:
                return min(delay, self._max_retry_delay_seconds)
        return min(self._backoff_base_seconds * (2 ** (attempt - 1)), self._max_retry_delay_seconds)

    def _wait_before_retry(self, method: str, path: str, attempt: int, response: httpx.Response | None = None) -> None:
        delay = self._retry_delay(attempt, response)
        logger.warning(
            "供应商只读请求准备重试 method=%s path=%s attempt=%s delay_seconds=%.3f",
            method,
            path,
            attempt + 1,
            delay,
        )
        self._sleep(delay)

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
    ) -> Any:
        if not self._api_key:
            raise SupplierConfigurationError("供应商 API Key 未配置")
        method = "POST" if form is not None else "GET"
        response = None
        last_error = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.request(method, path, params=params, data=form, headers={"token": self._api_key})
                logger.info("供应商只读请求完成 method=%s path=%s status=%s attempt=%s", method, path, response.status_code, attempt)
            except httpx.TimeoutException as exc:
                last_error = SupplierTimeoutError("供应商请求超时")
                logger.warning("供应商只读请求超时 method=%s path=%s attempt=%s", method, path, attempt)
                if attempt == self._max_attempts:
                    raise last_error from exc
                self._wait_before_retry(method, path, attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = SupplierUnavailableError("供应商服务不可用")
                logger.warning("供应商只读请求失败 method=%s path=%s attempt=%s", method, path, attempt)
                if attempt == self._max_attempts:
                    raise last_error from exc
                self._wait_before_retry(method, path, attempt)
                continue
            if response.status_code == 429:
                last_error = SupplierRateLimitError("供应商请求受限")
            elif response.status_code >= 500:
                last_error = SupplierUnavailableError("供应商服务不可用")
            else:
                break
            if attempt == self._max_attempts:
                raise last_error
            self._wait_before_retry(method, path, attempt, response)
        if response is None:
            raise last_error or SupplierUnavailableError("供应商服务不可用")
        if response.status_code >= 400:
            raise SupplierBusinessError("供应商拒绝请求")
        try:
            payload = response.json()
        except ValueError as exc:
            if path == QUOTES_ENDPOINT:
                self.last_quote_response_shape = {"type": "non_json"}
            raise SupplierInvalidResponseError("供应商响应格式无效") from exc
        if path == QUOTES_ENDPOINT:
            self.last_quote_response_shape = response_shape(payload)
        if (
            not isinstance(payload, dict)
            or type(payload.get("code")) is not int
            or not isinstance(payload.get("msg"), str)
            or not isinstance(payload.get("time"), str)
            or "data" not in payload
            or payload["code"] != 1
        ):
            if isinstance(payload, dict) and payload.get("code") == 0:
                raise SupplierBusinessError("供应商返回业务错误")
            raise SupplierInvalidResponseError("供应商响应结构无效")
        return payload["data"]

    def list_products(self) -> list[SupplierProductItem]:
        data = self._request(PRODUCTS_ENDPOINT)
        if not isinstance(data, list):
            raise SupplierInvalidResponseError("供应商商品响应结构无效")
        items = []
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("name"), str):
                raise SupplierInvalidResponseError("供应商商品字段无效")
            suffix = row.get("suffix")
            if suffix is not None and not isinstance(suffix, str):
                raise SupplierInvalidResponseError("供应商商品字段无效")
            items.append(SupplierProductItem(str(row["id"]), row["name"], suffix))
        return items

    def _require_supported_region_product(self, product_type_id: int) -> None:
        if product_type_id != SUPPORTED_REGION_PRODUCT_ID:
            raise SupplierCapabilityUnsupportedError("当前仅实现官方已核对的产品 2 地区与库存查询")

    def list_countries(self, product_type_id: int) -> list[SupplierRegionItem]:
        self._require_supported_region_product(product_type_id)
        data = self._request(COUNTRIES_ENDPOINT, {"product_type_id": 2})
        rows = data.get("countrys") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise SupplierInvalidResponseError("供应商国家响应结构无效")
        items = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id") or not row.get("code") or not row.get("name"):
                raise SupplierInvalidResponseError("供应商国家字段无效")
            items.append(SupplierRegionItem(str(row["id"]), str(row["code"]).upper(), str(row["name"])))
        return items

    def list_cities(self, product_type_id: int, country_code: str | None = None,
                    country_id: str | None = None) -> list[SupplierRegionItem]:
        self._require_supported_region_product(product_type_id)
        params: dict[str, Any] = {"product_type_id": 2}
        if country_id:
            if not country_id.isdecimal():
                raise SupplierCapabilityUnsupportedError("国家 ID 格式无效")
            params["country_id"] = country_id
        if country_code:
            params["country_code"] = country_code.upper()
        data = self._request(CITIES_ENDPOINT, params)
        if not isinstance(data, list):
            raise SupplierInvalidResponseError("供应商城市响应结构无效")
        items = []
        for row in data:
            required = ("id", "name", "country_id", "country_code")
            if not isinstance(row, dict) or any(row.get(field) in (None, "") for field in required):
                raise SupplierInvalidResponseError("供应商城市字段无效")
            items.append(SupplierRegionItem(
                external_country_ref=str(row["country_id"]),
                country_code=str(row["country_code"]).upper(),
                country_name=str(row.get("country_name") or row["country_code"]),
                external_city_ref=str(row["id"]),
                city_name=str(row["name"]),
            ))
        return items

    def get_inventory(self, product_type_id: int, country_code: str) -> list[InventoryItem]:
        self._require_supported_region_product(product_type_id)
        data = self._request(INVENTORY_ENDPOINT, {"product_type_id": 2, "country_code": country_code.upper()})
        if not isinstance(data, list):
            raise SupplierInvalidResponseError("供应商库存响应结构无效")
        items = []
        for row in data:
            if not isinstance(row, dict) or row.get("sku_status") not in (0, 1) or not row.get("country_code"):
                raise SupplierInvalidResponseError("供应商库存字段无效")
            items.append(InventoryItem(
                country_code=str(row["country_code"]).upper(),
                city_name=str(row["city_name"]) if row.get("city_name") else None,
                availability="available" if row["sku_status"] == 0 else "tight",
            ))
        return items

    def check_inventory(
        self,
        product_type_id: int,
        quantity: int,
        country_code: str,
        duration_code: int | None = None,
    ) -> InventoryCheckResult:
        self._require_supported_region_product(product_type_id)
        if quantity < 1 or quantity > 300 or duration_code not in (None, 0, 1, 2, 3, 4):
            raise SupplierCapabilityUnsupportedError("库存检测数量或时长超出官方文档范围")
        form: dict[str, Any] = {"product_type_id": 2, "num": quantity, "country": country_code.upper()}
        if duration_code is not None:
            form["timelen"] = duration_code
        data = self._request(INVENTORY_CHECK_ENDPOINT, form=form)
        if not isinstance(data, dict) or not isinstance(data.get("is_sufficient"), bool):
            raise SupplierInvalidResponseError("供应商库存检测响应结构无效")
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            raise SupplierInvalidResponseError("供应商库存检测网段结构无效")
        normalized_segments = []
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("cidr"), str) or not isinstance(segment.get("is_sufficient"), bool):
                raise SupplierInvalidResponseError("供应商库存检测网段字段无效")
            normalized_segments.append((segment["cidr"], segment["is_sufficient"]))
        return InventoryCheckResult(data["is_sufficient"], tuple(normalized_segments))

    @staticmethod
    def _money(value: Any) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise SupplierInvalidResponseError("供应商报价金额无效") from exc
        if not amount.is_finite() or amount < 0:
            raise SupplierInvalidResponseError("供应商报价金额无效")
        return amount

    def get_quotes(self, **parameters) -> list[QuoteItem]:
        product_type_id = parameters.get("product_type_id")
        if product_type_id not in (None, 11):
            raise SupplierCapabilityUnsupportedError("报价接口仅支持完整单价表或产品 11 试算")
        if product_type_id == 11:
            country = str(parameters.get("country") or "").upper()
            bandwidth = parameters.get("bandwidth_num")
            bill_timelen = parameters.get("bill_timelen")
            if country not in {"US", "DE", "SG"} or not isinstance(bandwidth, int) or bandwidth < 10 or bandwidth > 1000 or bandwidth % 10 or bill_timelen not in {1, 2, 3}:
                raise SupplierCapabilityUnsupportedError("产品 11 试算参数超出官方文档范围")
            query = {
                "product_type_id": 11,
                "country": country,
                "bandwidth_num": bandwidth,
                "bill_timelen": bill_timelen,
            }
        else:
            if any(value is not None for key, value in parameters.items() if key != "product_type_id"):
                raise SupplierCapabilityUnsupportedError("完整单价表查询不接受额外参数")
            query = {}
        data = self._request(QUOTES_ENDPOINT, query)
        if not isinstance(data, dict):
            raise SupplierInvalidResponseError("供应商报价响应结构无效")
        if product_type_id == 11:
            required = ("product_type_id", "userUnitPrice", "realPrice", "days", "currency", "area", "bandwidth_num")
            if (any(field not in data for field in required) or type(data.get("product_type_id")) is not int
                    or data["product_type_id"] != 11 or type(data["days"]) is not int or data["days"] <= 0
                    or not isinstance(data["currency"], str) or not data["currency"]
                    or not isinstance(data["area"], str) or not data["area"]
                    or type(data["bandwidth_num"]) is not int or data["bandwidth_num"] != bandwidth):
                raise SupplierInvalidResponseError("供应商产品 11 报价字段无效")
            return [QuoteItem(
                external_product_ref="11",
                unit_cost=self._money(data["userUnitPrice"]),
                total_cost=self._money(data["realPrice"]),
                duration_days=data["days"],
                country_code=country,
                currency=str(data["currency"]),
                is_custom=bool(data.get("isUseCustomUnitPrice", False)),
            )]
        return self._normalize_price_table(data)

    def _normalize_price_table(self, data: dict[str, Any]) -> list[QuoteItem]:
        quotes: list[QuoteItem] = []
        for product_ref, product_data in data.items():
            if product_ref == "customPriceBalance" or not isinstance(product_data, dict):
                continue
            if "userUnitPrice" in product_data:
                quotes.append(self._quote_from_price(product_ref, product_data))
                continue
            for duration, price_data in product_data.items():
                if not str(duration).isdigit() or not isinstance(price_data, dict) or "userUnitPrice" not in price_data:
                    raise SupplierInvalidResponseError("供应商报价档位无效")
                duration_days = int(duration)
                quotes.append(self._quote_from_price(product_ref, price_data, duration_days))
                for country_code, country_price in price_data.items():
                    if country_code in {"originUnitPrice", "userUnitPrice", "isCustom"}:
                        continue
                    if not isinstance(country_price, dict) or "userUnitPrice" not in country_price:
                        raise SupplierInvalidResponseError("供应商国家报价档位无效")
                    quotes.append(self._quote_from_price(product_ref, country_price, duration_days, country_code))
        if not quotes:
            raise SupplierInvalidResponseError("供应商报价为空")
        return quotes

    def _quote_from_price(
        self,
        product_ref: str,
        data: dict[str, Any],
        duration_days: int | None = None,
        country_code: str | None = None,
    ) -> QuoteItem:
        return QuoteItem(
            external_product_ref=product_ref,
            unit_cost=self._money(data["userUnitPrice"]),
            duration_days=duration_days,
            country_code=country_code.upper() if country_code else None,
            is_custom=bool(data.get("isCustom", False)),
        )
