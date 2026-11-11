"""资源池 1（全球代理）原生静态住宅写接口：购买 / 续费 / 退单 / 子账号 / 号段。

文档：https://develop.quanqiudaili.com/  → 静态住宅（原生）时长子账号
关键差异（相对 985）：购买是**同步**返回子账号，不需要轮询；时长档含 360 天。
"""

from datetime import datetime, timezone

import httpx
import pytest

from app.suppliers.base import (
    SupplierBusinessError, SupplierCapabilityUnsupportedError, SupplierInvalidResponseError,
)
from app.suppliers.global_proxy import (
    BUY_ORDER_ENDPOINT, REFUND_ENDPOINT, RENEW_ORDER_ENDPOINT, SUB_ACCOUNTS_ENDPOINT,
    GlobalProxyAdapter, NATIVE_PRODUCT_TYPE_ID,
)

API_KEY = "test-token"


def adapter(handler):
    return GlobalProxyAdapter(api_key=API_KEY, transport=httpx.MockTransport(handler),
                              sleep=lambda _: None)


def reply(data, code=1, msg="ok"):
    return httpx.Response(200, json={"code": code, "msg": msg, "time": "1790491344", "data": data})


def account_row(**overrides):
    row = {
        "password": "pwd-1",
        "username": "user-1",
        "id": 9,
        "port": 5001,
        "agree": "SOCKS5",
        "target": "proxy.quanqiudaili.com",
        "expiresIn": 1747272617,
        "ip": "149.52.10.11",
        "country": "US",
        "countryName": "美国",
        "city": "Los Angeles",
        "state": "California",
        "remark": "ORD-1",
        "order_product_buy_id": 16874,
    }
    row.update(overrides)
    return row


def test_buy_static_ip_sends_documented_form_and_parses_accounts():
    captured = {}

    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == BUY_ORDER_ENDPOINT
        assert request.headers["token"] == API_KEY
        captured["body"] = request.content.decode()
        return reply({"sub_accounts": [account_row()], "order_number": "20251108ABC"})

    result = adapter(handler).buy_static_ip(
        quantity=1, timelen=1, country_code="us", city="Los Angeles", protocol="SOCKS5",
        segments=[("149.52", 1)], exclude_segments="38.30", remark="ORD-1",
    )

    body = captured["body"]
    assert "product_type_id=3" in body
    assert "num=1" in body
    assert "timelen=1" in body
    assert "country=US" in body
    assert "city=Los+Angeles" in body
    assert "appoint_ip%5B0%5D%5Bip_str%5D=149.52" in body
    assert "appoint_ip%5B0%5D%5Bcount%5D=1" in body
    assert "exclude_ip_str=38.30" in body

    assert result.order_number == "20251108ABC"
    account = result.accounts[0]
    assert (account.external_id, account.username, account.ip, account.port) == ("9", "user-1", "149.52.10.11", 5001)
    assert account.expires_at == datetime.fromtimestamp(1747272617, timezone.utc).replace(tzinfo=None)
    assert account.order_product_buy_id == 16874


def test_buy_rejects_account_count_mismatch():
    def handler(request):
        return reply({"sub_accounts": [account_row()], "order_number": "X"})

    with pytest.raises(SupplierInvalidResponseError, match="数量不符"):
        adapter(handler).buy_static_ip(quantity=2, timelen=1, country_code="US")


@pytest.mark.parametrize("kwargs", [
    {"quantity": 0, "timelen": 1, "country_code": "US"},
    {"quantity": 1, "timelen": 5, "country_code": "US"},
    {"quantity": 1, "timelen": 1, "country_code": "USA"},
    {"quantity": 1, "timelen": 1, "country_code": "US", "protocol": "FTP"},
    {"quantity": 1, "timelen": 1, "country_code": "US", "segments": [("149", 1)]},
])
def test_buy_validates_documented_ranges(kwargs):
    with pytest.raises(SupplierCapabilityUnsupportedError):
        adapter(lambda request: reply({})).buy_static_ip(**kwargs)


def test_business_error_is_surfaced():
    def handler(request):
        return reply(None, code=0, msg="余额不足")

    with pytest.raises(SupplierBusinessError):
        adapter(handler).buy_static_ip(quantity=1, timelen=1, country_code="US")


def test_renew_static_ip_returns_new_expiry():
    def handler(request):
        assert request.url.path == RENEW_ORDER_ENDPOINT
        body = request.content.decode()
        assert "sub_account_ids=9%2C10" in body
        assert "timelen=4" in body
        return reply({
            "sub_accounts": [{"id": 9, "expiresIn": 1760000000, "expiresDate": "2026-10-09"}],
            "order_number": "RENEW-1",
        })

    result = adapter(handler).renew_static_ip(sub_account_ids=[9, 10], timelen=4)
    assert result.order_number == "RENEW-1"
    assert result.expires == (("9", datetime.fromtimestamp(1760000000, timezone.utc).replace(tzinfo=None)),)


def test_refund_sends_repeated_ids_and_requires_remark():
    captured = {}

    def handler(request):
        assert request.url.path == REFUND_ENDPOINT
        captured["body"] = request.content.decode()
        return reply(None)

    adapter(handler).request_refund(order_product_buy_id=16874, sub_account_ids=[9, 10],
                                    remark="客户取消")
    body = captured["body"]
    assert "order_product_buy_id=16874" in body
    assert "ids%5B%5D=9" in body and "ids%5B%5D=10" in body
    assert "remark=%E5%AE%A2%E6%88%B7%E5%8F%96%E6%B6%88" in body

    with pytest.raises(SupplierCapabilityUnsupportedError):
        adapter(handler).request_refund(order_product_buy_id=16874, sub_account_ids=[9], remark="  ")
    with pytest.raises(SupplierCapabilityUnsupportedError):
        adapter(handler).request_refund(order_product_buy_id=0, sub_account_ids=[9], remark="x")


def test_list_sub_accounts_and_segments():
    def handler(request):
        if request.url.path == SUB_ACCOUNTS_ENDPOINT:
            assert request.url.params["product_type_id"] == "3"
            return reply({"total": 1, "rows": [account_row()]})
        return reply({"current_page": 1, "last_page": 1, "total": 2,
                      "data": [{"ipStr": "149.52", "type": 1, "remark": "充足", "skuStatus": 0},
                               {"ipStr": "38.30", "type": 1, "remark": "紧缺", "skuStatus": 1}]})

    client = adapter(handler)
    accounts, total = client.list_sub_accounts(page=1, page_size=10, country_code="us")
    assert total == 1 and accounts[0].username == "user-1"

    segments, segment_total = client.list_ip_segments(country_code="US", city_code="Los Angeles")
    assert segments == ["149.52", "38.30"] and segment_total == 2


def test_native_product_is_passed_through_for_region_queries():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if request.url.path.endswith("countryList"):
            return reply([{"id": 653, "name": "美国", "code": "US"}])
        if request.url.path.endswith("cityList"):
            return reply([{"id": 3911, "name": "Los Angeles", "country_id": 653, "country_code": "US"}])
        return reply([{"country_code": "US", "city_name": "Los Angeles", "sku_status": 0}])

    client = adapter(handler)
    assert client.list_countries(NATIVE_PRODUCT_TYPE_ID)[0].country_code == "US"
    assert client.list_cities(NATIVE_PRODUCT_TYPE_ID, country_code="US")[0].city_name == "Los Angeles"
    assert client.get_inventory(NATIVE_PRODUCT_TYPE_ID, "US")[0].availability == "available"
    assert all(params["product_type_id"] == "3" for params in seen)
