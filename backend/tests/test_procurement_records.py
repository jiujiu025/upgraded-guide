"""采购记录页（后端）：只读列表 + 详情，且**绝不回显上游凭据原文**。

采购买的是扣钱动作，出问题时管理员要能看到「哪张订单、哪个上游、什么错、有没有
拿到线路」。但 `response_payload` 里是上游返回的 IP/端口/账号/密码，`request_payload`
是内部请求体——这两个字段一旦进接口，前端缓存、浏览器历史和日志里就都有一份凭据。
所以这里用用例把边界钉死：
- 列表与详情都不返回这两个字段；
- 详情只回显白名单里的业务参数和"交付了几个账号"。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, Order, Procurement, Product, Region, Supplier, User
from app.models.base import Base

SECRET_USERNAME = "acct_secret_user"
SECRET_PASSWORD = "acct-secret-pass"

RESPONSE_PAYLOAD = (
    '[{"ip": "203.0.113.9", "port": 1080, "username": "' + SECRET_USERNAME
    + '", "expires_at": null}]'
)
REQUEST_PAYLOAD = (
    '{"order_no": "ORD-1", "quantity": 2, "billing_days": 30, "country": "US",'
    ' "city": "New York", "segment": "1.1.1", "trigger": "payment", "internal_note": "不要外泄"}'
)


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="root", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    customer_user = User(username="buyer", password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户")
    sales_user = User(username="seller", password_hash=hash_password("Sales_123456"),
                      role="sales", display_name="销售")
    db.add_all([admin, customer_user, sales_user])
    db.flush()

    region = Region(name="美国-纽约", country_code="US")
    db.add(region)
    db.flush()

    product = Product(name="静态住宅-美国", product_type="static_residential", price=199.0,
                      region_id=region.id, resource_category="static_residential")
    db.add(product)
    db.flush()

    customer = Customer(name="上海某跨境公司", email="buyer@example.com", user_id=customer_user.id)
    db.add(customer)
    db.flush()

    order_one = Order(order_no="ORD-1", customer_id=customer.id, product_id=product.id, amount=398.0,
                      quantity=2, billing_days=30, unit_price=199.0, total_amount=398.0,
                      region_id=region.id, region_name="美国-纽约", product_name="静态住宅-美国",
                      payment_status="paid", status="pending_delivery")
    order_two = Order(order_no="ORD-2", customer_id=customer.id, product_id=product.id, amount=199.0,
                      quantity=1, billing_days=30, unit_price=199.0, total_amount=199.0,
                      region_id=region.id, region_name="美国-纽约", product_name="静态住宅-美国",
                      payment_status="paid", status="pending_delivery")
    db.add_all([order_one, order_two])
    db.flush()

    supplier = Supplier(code="quanqiudaili", name="全球代理", adapter_type="global_proxy",
                        base_url="https://example.invalid", secret_ref="env:SUPPLIER_KEY")
    db.add(supplier)
    db.flush()

    db.add_all([
        Procurement(order_id=order_one.id, line_index=0, supplier_id=supplier.id, supplier_code="quanqiudaili",
                    status="succeeded", external_order_no="UP-9001", request_payload=REQUEST_PAYLOAD,
                    response_payload=RESPONSE_PAYLOAD, line_id=321),
        Procurement(order_id=order_two.id, line_index=0, supplier_id=supplier.id, supplier_code="quanqiudaili",
                    status="failed", error_code="BALANCE_LOW", error_message="上游余额不足",
                    request_payload=REQUEST_PAYLOAD),
        Procurement(order_id=order_two.id, line_index=1, supplier_id=supplier.id, supplier_code="quanqiudaili",
                    status="manual", error_code="UNSUPPORTED_PERIOD", error_message="上游没有 45 天档位"),
        Procurement(order_id=order_two.id, line_index=2, supplier_id=None, supplier_code=None, status="pending"),
    ])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_sees_all_procurements_newest_first(api):
    headers = login(api, "root", "Admin_123456")
    response = api.get("/api/admin/procurements", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 4
    assert body["pages"] == 1
    assert body["counts"] == {"pending": 1, "succeeded": 1, "failed": 1, "manual": 1}
    assert [item["status"] for item in body["items"]][0] == "pending"  # 最新的在前
    assert body["suppliers"] == [{"id": 1, "code": "quanqiudaili", "name": "全球代理"}]

    succeeded = next(item for item in body["items"] if item["status"] == "succeeded")
    assert succeeded["order_no"] == "ORD-1"
    assert succeeded["customer_name"] == "上海某跨境公司"
    assert succeeded["external_order_no"] == "UP-9001"
    assert succeeded["line_id"] == 321
    assert succeeded["has_response"] is True


def test_status_filter_does_not_shrink_counts(api):
    headers = login(api, "root", "Admin_123456")
    body = api.get("/api/admin/procurements", params={"status": "failed"}, headers=headers).json()

    assert body["total"] == 1
    assert [item["error_code"] for item in body["items"]] == ["BALANCE_LOW"]
    # 计数仍给全量分布，否则选中一个状态后下拉框只剩一个选项
    assert body["counts"] == {"pending": 1, "succeeded": 1, "failed": 1, "manual": 1}


def test_keyword_matches_order_no_external_no_and_error_code(api):
    headers = login(api, "root", "Admin_123456")

    by_order = api.get("/api/admin/procurements", params={"keyword": "ORD-2"}, headers=headers).json()
    assert by_order["total"] == 3

    by_external = api.get("/api/admin/procurements", params={"keyword": "UP-9001"}, headers=headers).json()
    assert by_external["total"] == 1
    assert by_external["items"][0]["status"] == "succeeded"

    by_error = api.get("/api/admin/procurements", params={"keyword": "余额不足"}, headers=headers).json()
    assert by_error["total"] == 1


def test_keyword_escapes_like_wildcards(api):
    """搜索框里输入 % 不能等于"不过滤"。"""
    headers = login(api, "root", "Admin_123456")
    body = api.get("/api/admin/procurements", params={"keyword": "%"}, headers=headers).json()
    assert body["total"] == 0


def test_pagination_and_supplier_filter(api):
    headers = login(api, "root", "Admin_123456")

    page_one = api.get("/api/admin/procurements", params={"page": 1, "page_size": 2}, headers=headers).json()
    page_two = api.get("/api/admin/procurements", params={"page": 2, "page_size": 2}, headers=headers).json()
    assert len(page_one["items"]) == 2 and len(page_two["items"]) == 2
    assert page_one["pages"] == 2
    assert {item["id"] for item in page_one["items"]} & {item["id"] for item in page_two["items"]} == set()

    # 没挂供应商的那条也要能被 supplier_id 过滤掉，而不是被 inner join 吞掉
    by_supplier = api.get("/api/admin/procurements", params={"supplier_id": 1}, headers=headers).json()
    assert by_supplier["total"] == 3

    # 记录在、但 supplier_id 为空的行仍然出现在全量列表里
    orphan = next(item for item in api.get("/api/admin/procurements", headers=headers).json()["items"]
                  if item["status"] == "pending")
    assert orphan["supplier_id"] is None and orphan["supplier_name"] is None


def test_customer_and_sales_cannot_read_procurements(api):
    for username, password in (("buyer", "Customer_123456"), ("seller", "Sales_123456")):
        headers = login(api, username, password)
        assert api.get("/api/admin/procurements", headers=headers).status_code == 403
        assert api.get("/api/admin/procurements/1", headers=headers).status_code == 403


def test_detail_returns_whitelisted_request_summary_only(api):
    headers = login(api, "root", "Admin_123456")
    response = api.get("/api/admin/procurements/1", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["request_summary"] == {
        "order_no": "ORD-1", "quantity": 2, "billing_days": 30,
        "country": "US", "city": "New York", "segment": "1.1.1", "trigger": "payment",
    }
    assert body["delivered_accounts"] == 1

    # 两个原始字段一律不出现，白名单外的请求字段也不回显
    assert "request_payload" not in body and "response_payload" not in body
    assert "internal_note" not in body["request_summary"]
    assert SECRET_USERNAME not in response.text
    assert SECRET_PASSWORD not in response.text


def test_detail_handles_missing_payload(api):
    headers = login(api, "root", "Admin_123456")
    body = api.get("/api/admin/procurements/2", headers=headers).json()
    assert body["request_summary"]["order_no"] == "ORD-1"
    assert body["delivered_accounts"] == 0


def test_detail_404_and_invalid_status(api):
    headers = login(api, "root", "Admin_123456")
    assert api.get("/api/admin/procurements/999", headers=headers).status_code == 404
    assert api.get("/api/admin/procurements", params={"status": "whatever"}, headers=headers).status_code == 422
