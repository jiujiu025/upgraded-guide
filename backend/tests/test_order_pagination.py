"""订单列表分页：页大小、总数、状态分布、关键词与角色隔离。

这套接口原来一律全表返回。改成分页后最容易坏的三件事都在这里钉住：
1. `total` 必须是"满足条件的总数"，不是当页条数；
2. `status_counts` 必须是**未按状态筛选**的全量分布，否则选中某个状态后
   下拉框里只剩它自己，用户再也切不回去；
3. 销售/客户的范围过滤不能被分页绕过去。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, Order, User
from app.models.base import Base


def _order(customer_id: int, seq: int, status: str) -> Order:
    return Order(
        order_no=f"ORD{seq:06d}",
        customer_id=customer_id,
        product_id=1,
        amount=100.0,
        quantity=1,
        billing_days=30,
        unit_price=100.0,
        total_amount=100.0,
        region_name="美国",
        product_name="美国原生独享",
        status=status,
        payment_status="paid" if status != "pending_confirm" else "unpaid",
    )


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 B")
    user_a = User(username="customer_a", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户 A")
    db.add_all([admin, sales_a, sales_b, user_a])
    db.flush()

    customer_a = Customer(name="客户 A", email="a@example.com", user_id=user_a.id, sales_id=sales_a.id)
    customer_b = Customer(name="客户 B", email="b@example.com", sales_id=sales_b.id)
    db.add_all([customer_a, customer_b])
    db.flush()

    # A：30 单（20 待确认 + 10 已完成），B：5 单，用来验证分页与角色隔离
    for index in range(20):
        db.add(_order(customer_a.id, index + 1, "pending_confirm"))
    for index in range(10):
        db.add(_order(customer_a.id, index + 101, "completed"))
    for index in range(5):
        db.add(_order(customer_b.id, index + 201, "pending_confirm"))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.customer_ids = {"a": customer_a.id, "b": customer_b.id}
    yield client
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _auth(api, username, password):
    response = api.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_customer_order_list_is_paged_with_full_total(api):
    headers = _auth(api, "customer_a", "Customer_123456")

    response = api.get("/api/customer/orders?page=2&page_size=10", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 30
    assert body["page"] == 2
    assert len(body["items"]) == 10


def test_status_filter_does_not_collapse_the_status_counts(api):
    headers = _auth(api, "customer_a", "Customer_123456")

    response = api.get("/api/customer/orders?status=completed&page_size=10", headers=headers)

    body = response.json()
    assert body["total"] == 10
    assert {item["status"] for item in body["items"]} == {"completed"}
    # 全量分布仍在，前端下拉框才不会只剩"已完成"
    assert body["status_counts"] == {"pending_confirm": 20, "completed": 10}


def test_keyword_matches_order_no_literally(api):
    headers = _auth(api, "customer_a", "Customer_123456")

    exact = api.get("/api/customer/orders?keyword=ORD000007", headers=headers).json()
    wildcard = api.get("/api/customer/orders?keyword=%25", headers=headers).json()

    assert exact["total"] == 1
    assert exact["items"][0]["order_no"] == "ORD000007"
    # 输入一个 % 不等于"不过滤"
    assert wildcard["total"] == 0


def test_page_beyond_last_returns_empty_but_keeps_total(api):
    headers = _auth(api, "customer_a", "Customer_123456")

    body = api.get("/api/customer/orders?page=99&page_size=25", headers=headers).json()

    assert body["items"] == []
    assert body["total"] == 30


def test_sales_only_sees_orders_of_own_customers(api):
    headers = _auth(api, "sales_a", "Sales_123456")

    body = api.get("/api/sales/orders?page_size=50", headers=headers).json()

    assert body["total"] == 30
    assert {item["customer_id"] for item in body["items"]} == {api.customer_ids["a"]}


def test_admin_can_filter_by_customer(api):
    headers = _auth(api, "admin", "Admin_123456")

    body = api.get(f"/api/admin/orders?customer_id={api.customer_ids['b']}", headers=headers).json()

    assert body["total"] == 5
    assert {item["customer_id"] for item in body["items"]} == {api.customer_ids["b"]}


def test_admin_page_is_bounded_by_state_not_by_row_count(api):
    headers = _auth(api, "admin", "Admin_123456")

    body = api.get("/api/admin/orders?page_size=100", headers=headers).json()

    assert body["total"] == 35
    assert len(body["items"]) == 35
    assert body["status_counts"] == {"pending_confirm": 25, "completed": 10}


def test_page_size_above_limit_is_rejected(api):
    headers = _auth(api, "admin", "Admin_123456")

    assert api.get("/api/admin/orders?page_size=101", headers=headers).status_code == 422
    assert api.get("/api/admin/orders?page=0", headers=headers).status_code == 422
