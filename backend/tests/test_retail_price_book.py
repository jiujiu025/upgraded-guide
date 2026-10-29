import pytest
from pydantic import ValidationError

from app.api.routes import AdminResourceOrderIn
from app.data.retail_price_seed import RETAIL_PRICE_GROUPS, RETAIL_PRICE_SOURCE


def test_retail_price_book_only_contains_month_or_longer_terms():
    expanded = {
        (category, country, days): price
        for category, country, prices in RETAIL_PRICE_GROUPS
        for days, price in prices
    }
    assert all(days in {30, 90, 180, 360} for _, _, days in expanded)
    assert expanded[("static_residential_isp", "HK", 30)] == "80"
    assert expanded[("static_residential_isp", "HK", 90)] == "240"
    assert expanded[("static_residential_isp", "HK", 180)] == "480"
    assert expanded[("static_residential_isp", "HK", 360)] == "960"
    assert expanded[("static_residential", "HK", 30)] == "60"
    assert RETAIL_PRICE_SOURCE == "全球代理静态IP各国价格_2026-10-27"


def test_resource_order_rejects_client_pricing_and_short_terms():
    payload = {
        "customer_id": 1,
        "product_id": 1,
        "supplier_product_id": 1,
        "supplier_region_id": 1,
        "supplier_segment_snapshot_id": 1,
        "supplier_quote_id": 1,
        "idempotency_key": "00000000-0000-4000-8000-000000000001",
        "resource_category": "static_residential_isp",
        "quantity": 1,
        "billing_days": 30,
    }
    assert AdminResourceOrderIn.model_validate(payload).billing_days == 30
    with pytest.raises(ValidationError):
        AdminResourceOrderIn.model_validate({**payload, "unit_price": 1})
    with pytest.raises(ValidationError):
        AdminResourceOrderIn.model_validate({**payload, "billing_days": 7})
