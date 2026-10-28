from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class SupplierError(Exception):
    code = "SUPPLIER_ERROR"


class SupplierConfigurationError(SupplierError):
    code = "SUPPLIER_CONFIGURATION_ERROR"


class SupplierTimeoutError(SupplierError):
    code = "SUPPLIER_TIMEOUT"


class SupplierRateLimitError(SupplierError):
    code = "SUPPLIER_RATE_LIMITED"


class SupplierUnavailableError(SupplierError):
    code = "SUPPLIER_UNAVAILABLE"


class SupplierInvalidResponseError(SupplierError):
    code = "SUPPLIER_INVALID_RESPONSE"


class SupplierBusinessError(SupplierError):
    code = "SUPPLIER_BUSINESS_ERROR"


class SupplierCapabilityUnsupportedError(SupplierError):
    code = "SUPPLIER_CAPABILITY_UNSUPPORTED"


class ProcurementDisabledError(SupplierError):
    code = "SUPPLIER_PROCUREMENT_DISABLED"


@dataclass(frozen=True)
class SupplierProductItem:
    external_ref: str
    name: str
    suffix: str | None


@dataclass(frozen=True)
class SupplierRegionItem:
    external_country_ref: str
    country_code: str
    country_name: str
    external_city_ref: str = ""
    city_name: str | None = None


@dataclass(frozen=True)
class InventoryItem:
    country_code: str
    city_name: str | None
    availability: str


@dataclass(frozen=True)
class InventoryCheckResult:
    is_sufficient: bool
    segments: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class QuoteItem:
    external_product_ref: str
    unit_cost: Decimal
    total_cost: Decimal | None = None
    duration_days: int | None = None
    country_code: str | None = None
    currency: str = "CNY"
    is_custom: bool = False


class ReadOnlySupplierAdapter(Protocol):
    def list_products(self) -> list[SupplierProductItem]: ...

    def list_countries(self, product_type_id: int) -> list[SupplierRegionItem]: ...

    def list_cities(self, product_type_id: int, country_code: str | None = None) -> list[SupplierRegionItem]: ...

    def get_inventory(self, product_type_id: int, country_code: str) -> list[InventoryItem]: ...

    def check_inventory(
        self, product_type_id: int, quantity: int, country_code: str, duration_code: int | None = None,
    ) -> InventoryCheckResult: ...

    def get_quotes(self, **parameters) -> list[QuoteItem]: ...


def reject_procurement() -> None:
    """只读阶段始终拒绝采购，配置开关也不能绕过该守卫。"""
    raise ProcurementDisabledError("供应商采购能力未启用")
