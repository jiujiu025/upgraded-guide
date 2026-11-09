"""财务概览的销售佣金列表必须与上方汇总同区间（P2-3.4）。

之前列表是全时段累计：汇总说"这 30 天发了 300 元佣金"，下面列表却把去年的一起算进去，
同一页面两个口径。现在条件写在 join 上，区间内没佣金的销售仍显示 0。
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, Order, PaymentOrder, Product, Region, SalesCommissionEntry, User
from app.models.base import Base, utcnow
from app.services.session_service import create_session


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 B")
    db.add_all([admin, sales_a, sales_b])
    db.flush()

    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    product = Product(name="美国线路", product_type="独享静态住宅（普通）", price=100, billing_days=30, region_id=region.id)
    db.add(product)
    db.flush()
    customer = Customer(name="客户一", email="c@test.com", sales_id=sales_a.id)
    db.add(customer)
    db.flush()
    order = Order(order_no="ORD-TEST-1", customer_id=customer.id, product_id=product.id, amount=100,
                  unit_price=100, total_amount=100, region_id=region.id, region_name="美国",
                  product_name="美国线路", assigned_sales_id=sales_a.id)
    db.add(order)
    db.flush()
    payment = PaymentOrder(user_id=admin.id, purpose="order_payment",
                           amount_cents=10000, status="paid", channel="offline", order_id=order.id,
                           out_trade_no="TEST-1", idempotency_key="test-payment-1")
    db.add(payment)
    db.flush()

    def commission(sales, amount, days_ago):
        entry = SalesCommissionEntry(
            sales_id=sales.id, customer_id=customer.id, order_id=order.id, payment_order_id=payment.id,
            amount_cents=amount, basis_cents=10000, rate_basis_points=1500,
            idempotency_key=f"test:{sales.id}:{days_ago}", reason="测试",
        )
        entry.created_at = utcnow() - timedelta(days=days_ago)
        db.add(entry)
        return entry

    commission(sales_a, 300, 2)     # 区间内
    commission(sales_a, 900, 40)    # 区间外（早于 30 天窗口）
    db.commit()

    admin_session, _ = create_session(db, admin)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, admin_session.id, admin.auth_version)}"}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield {"client": TestClient(app), "headers": headers, "db": db, "sales_a": sales_a, "sales_b": sales_b}
    app.dependency_overrides.clear()


def test_commission_rows_respect_date_range(api):
    response = api["client"].get("/api/admin/finance/summary", headers=api["headers"],
                                 params={"date_from": (utcnow().date() - timedelta(days=29)).isoformat(),
                                         "date_to": utcnow().date().isoformat()})
    assert response.status_code == 200, response.text
    rows = {row["display_name"]: row["net_cents"] for row in response.json()["sales_commissions"]}
    assert rows["销售 A"] == 300      # 只算区间内的 300，不含 40 天前的 900
    assert rows["销售 B"] == 0        # 区间内没有佣金的销售也要出现


def test_commission_totals_match_list_scope(api):
    response = api["client"].get("/api/admin/finance/summary", headers=api["headers"],
                                 params={"date_from": (utcnow().date() - timedelta(days=29)).isoformat(),
                                         "date_to": utcnow().date().isoformat()})
    body = response.json()
    listed = sum(row["net_cents"] for row in body["sales_commissions"])
    assert listed == body["totals"]["commission_cents"]


def test_narrow_window_excludes_everything_older(api):
    today = utcnow().date().isoformat()
    response = api["client"].get("/api/admin/finance/summary", headers=api["headers"],
                                 params={"date_from": today, "date_to": today})
    assert response.status_code == 200
    rows = {row["display_name"]: row["net_cents"] for row in response.json()["sales_commissions"]}
    # 2 天前的佣金不在"今天"这个窗口里
    assert rows["销售 A"] == 0
