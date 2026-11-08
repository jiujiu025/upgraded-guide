"""财务导出：口径、格式与留痕。

导出的错误不会当场报错——它会变成财务手里一份数字不对的报表，然后在月底被发现。
所以这组用例盯的是那些"看起来没问题"的地方：

1. BOM 与表头：没有 BOM 的 CSV 在 Excel 里是乱码，财务第一步就打不开；
2. 金额必须按分换算成两位小数的元，且空值要留空而不是 0；
3. 时区：跨零点的一笔收款要落在上海的那一天，不是 UTC 的那一天；
4. 公式注入：客户名或备注以 `=` 开头会在 Excel 里被当公式执行；
5. 月报不能把「先充值再消费」算成两笔收入；
6. 每次导出都要留痕，哪怕中途断开。
"""
import csv
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.core.timeutil import local_day_range_utc
from app.db import get_db
from app.main import app
from app.models import (
    AuditLog, Customer, Order, PaymentOrder, PaymentRefund, Product, Region, User, WalletAccount,
    WalletTransaction,
)
from app.models.base import Base
from app.services.registration_service import registration_rate_limiter
from app.services.wallet_service import apply_wallet_delta

# 上海 2026-11-08 10:00 对应 UTC 02:00
NOW = datetime(2026, 11, 8, 2, 0, 0)


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = Session()

    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    product = Product(name="美国原生独享", product_type="原生独享", price=0, billing_days=30, region_id=region.id)
    root = User(username="root", password_hash=hash_password("Root_123456"), role="super_admin", display_name="老板")
    sales = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售甲")
    buyer = User(username="buyer", password_hash=hash_password("Buyer_123456"), role="customer", display_name="买家")
    db.add_all([product, root, sales, buyer])
    db.flush()
    db.add(Customer(name="客户甲", sales_id=sales.id, user_id=buyer.id))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.session_factory = Session
    registration_rate_limiter.clear()
    yield client
    app.dependency_overrides.clear()
    registration_rate_limiter.clear()
    db.close()
    engine.dispose()


def login(client, username, password):
    registration_rate_limiter.clear()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})


def as_root(client):
    login(client, "root", "Root_123456")


def add_order(client, *, order_no="ORD-T-1", amount=300.0, payment_status="unpaid", status="pending_payment",
              created_at=NOW, supplier_cost=None, product_name="美国原生独享"):
    db = client.session_factory()
    try:
        customer = db.scalar(select(Customer).where(Customer.name == "客户甲"))
        product = db.scalar(select(Product))
        sales = db.scalar(select(User).where(User.username == "sales_a"))
        order = Order(order_no=order_no, customer_id=customer.id, product_id=product.id,
                      amount=amount, unit_price=amount, total_amount=amount, quantity=1, billing_days=30,
                      payment_status=payment_status, status=status, product_name=product_name,
                      region_name="美国", assigned_sales_id=sales.id, created_at=created_at,
                      supplier_cost=supplier_cost)
        db.add(order)
        db.commit()
        return order.id
    finally:
        db.close()


def add_payment(client, *, out_trade_no="PAY-T-1", amount_cents=30000, purpose="order_payment",
                channel="offline", status="paid", paid_at=NOW, order_id=None, user_name="buyer"):
    db = client.session_factory()
    try:
        user = db.scalar(select(User).where(User.username == user_name))
        payment = PaymentOrder(out_trade_no=out_trade_no, idempotency_key=f"idem-{out_trade_no}",
                               user_id=user.id, purpose=purpose, order_id=order_id,
                               amount_cents=amount_cents, channel=channel, status=status, paid_at=paid_at)
        db.add(payment)
        db.commit()
        return payment.id
    finally:
        db.close()


def csv_rows(response):
    assert response.status_code == 200, response.text
    text = response.text.lstrip("\ufeff")
    return list(csv.reader(io.StringIO(text)))


# --- 格式 -------------------------------------------------------------------

def test_export_is_utf8_bom_csv_with_header(api):
    as_root(api)
    add_order(api)
    response = api.get("/api/admin/finance/exports/orders",
                       params={"date_from": "2026-11-01", "date_to": "2026-11-30"})
    assert response.status_code == 200
    # 少了这三个字节，Excel 双击打开就是乱码。
    assert response.content[:3] == b"\xef\xbb\xbf"
    assert response.headers["content-type"].startswith("text/csv")
    assert "finance-orders-20261101-20261130.csv" in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]

    rows = csv_rows(response)
    assert rows[0][0] == "订单号"
    assert rows[1][0] == "ORD-T-1"


def test_amounts_are_yuan_with_two_decimals(api):
    as_root(api)
    add_order(api, amount=1234.5, payment_status="paid", status="paid", supplier_cost=800)
    rows = csv_rows(api.get("/api/admin/finance/exports/orders",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    header, row = rows[0], rows[1]
    record = dict(zip(header, row))
    assert record["应收金额(元)"] == "1234.50"
    assert record["单价(元)"] == "1234.50"
    assert record["供应商成本(元)"] == "800.00"
    assert record["毛利(元)"] == "434.50"


def test_unpaid_order_leaves_received_amount_blank(api):
    as_root(api)
    add_order(api, payment_status="unpaid")
    rows = csv_rows(api.get("/api/admin/finance/exports/orders",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    record = dict(zip(rows[0], rows[1]))
    # 没收到钱就留空：写 0 会被财务当成"这笔已经收过但金额是零"。
    assert record["实收金额(元)"] == ""
    assert record["支付状态"] == "未支付"


def test_wallet_export_formats_cents_and_snapshot(api):
    as_root(api)
    db = api.session_factory()
    try:
        buyer = db.scalar(select(User).where(User.username == "buyer"))
        apply_wallet_delta(db, buyer.id, 10000, "offline_recharge", "wallet-export-1", remark="微信转账")
        db.commit()
    finally:
        db.close()

    rows = csv_rows(api.get("/api/admin/finance/exports/wallet",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["金额(元)"] == "100.00"
    assert record["变动前余额(元)"] == "0.00"
    assert record["变动后余额(元)"] == "100.00"
    assert record["流水类型"] == "线下充值"
    assert record["备注"] == "微信转账"


def test_negative_wallet_amount_keeps_sign(api):
    as_root(api)
    db = api.session_factory()
    try:
        buyer = db.scalar(select(User).where(User.username == "buyer"))
        apply_wallet_delta(db, buyer.id, 10000, "offline_recharge", "wallet-export-2")
        apply_wallet_delta(db, buyer.id, -3000, "order_payment", "wallet-export-3")
        db.commit()
    finally:
        db.close()

    rows = csv_rows(api.get("/api/admin/finance/exports/wallet",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    amounts = [dict(zip(rows[0], row))["金额(元)"] for row in rows[1:]]
    assert "-30.00" in amounts


# --- 时区与区间 --------------------------------------------------------------

def test_order_falls_on_shanghai_day_not_utc_day(api):
    as_root(api)
    # UTC 2026-11-07 17:30 = 上海 2026-11-08 01:30，属于 11-08
    add_order(api, order_no="ORD-LATE", created_at=datetime(2026, 11, 7, 17, 30, 0))

    on_08 = csv_rows(api.get("/api/admin/finance/exports/orders",
                             params={"date_from": "2026-11-08", "date_to": "2026-11-08"}))
    on_07 = csv_rows(api.get("/api/admin/finance/exports/orders",
                             params={"date_from": "2026-11-07", "date_to": "2026-11-07"}))
    assert [row[0] for row in on_08[1:]] == ["ORD-LATE"]
    assert on_07[1:] == []


def test_empty_range_still_returns_header_only(api):
    as_root(api)
    add_order(api, created_at=NOW)
    rows = csv_rows(api.get("/api/admin/finance/exports/orders",
                            params={"date_from": "2027-01-01", "date_to": "2027-01-31"}))
    # 空结果也要有表头：0 字节文件会让人以为导出失败了。
    assert len(rows) == 1
    assert rows[0][0] == "订单号"


def test_large_export_survives_chunk_boundary(api):
    """跨越分块边界的行不能丢。

    CSV 按 400 行一批拼出去，缓冲区每批都要清空。清空与收尾的判断写错，
    末尾那批就会静默少几行——一份"看起来正常"的报表，少掉的正是最后几笔。
    """
    as_root(api)
    from app.services.wallet_service import ensure_wallet

    db = api.session_factory()
    try:
        buyer = db.scalar(select(User).where(User.username == "buyer"))
        wallet = ensure_wallet(db, buyer.id)
        db.flush()
        for index in range(450):
            db.add(WalletTransaction(
                wallet_account_id=wallet.id, transaction_type="offline_recharge",
                amount_cents=100, balance_before_cents=0, balance_after_cents=100,
                idempotency_key=f"bulk-{index}", created_at=NOW,
            ))
        db.commit()
    finally:
        db.close()

    rows = csv_rows(api.get("/api/admin/finance/exports/wallet",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    assert len(rows) == 451  # 表头 + 450 行


def test_range_longer_than_limit_is_rejected(api):
    as_root(api)
    response = api.get("/api/admin/finance/exports/orders",
                       params={"date_from": "2025-01-01", "date_to": "2026-11-30"})
    assert response.status_code == 422
    assert "最多" in response.json()["detail"]


def test_reversed_range_is_rejected(api):
    as_root(api)
    response = api.get("/api/admin/finance/exports/orders",
                       params={"date_from": "2026-11-30", "date_to": "2026-11-01"})
    assert response.status_code == 422


def test_unknown_export_kind_is_404(api):
    as_root(api)
    assert api.get("/api/admin/finance/exports/secrets").status_code == 404


# --- 安全 -------------------------------------------------------------------

def test_sales_cannot_export_finance(api):
    login(api, "sales_a", "Sales_123456")
    response = api.get("/api/admin/finance/exports/orders",
                       params={"date_from": "2026-11-01", "date_to": "2026-11-30"})
    assert response.status_code == 403


def test_formula_like_customer_name_is_neutralised(api):
    as_root(api)
    add_order(api)
    db = api.session_factory()
    try:
        customer = db.scalar(select(Customer).where(Customer.name == "客户甲"))
        # 客户名由销售录入，可以带任何字符。开头的 = 在 Excel 里会被当公式执行。
        customer.name = "=HYPERLINK(\"http://evil\",\"点我\")"
        db.commit()
    finally:
        db.close()

    rows = csv_rows(api.get("/api/admin/finance/exports/orders",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["客户"].startswith("'=")
    assert record["客户"].endswith(")"), record["客户"]


def test_numeric_cells_are_not_escaped(api):
    as_root(api)
    add_order(api, amount=300.0, payment_status="paid", status="paid")
    rows = csv_rows(api.get("/api/admin/finance/exports/orders",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    record = dict(zip(rows[0], rows[1]))
    # 数字列不能加前导引号，否则 Excel 会把它当文本，后续求和全失效。
    assert not record["应收金额(元)"].startswith("'")


def test_export_writes_audit_row(api):
    as_root(api)
    add_order(api)
    api.get("/api/admin/finance/exports/orders",
            params={"date_from": "2026-11-01", "date_to": "2026-11-30"})

    db = api.session_factory()
    try:
        log = db.scalar(select(AuditLog).where(AuditLog.action == "export_finance"))
        assert log is not None
        assert log.resource_id == "orders"
        assert "2026-11-01" in log.detail and "行数=1" in log.detail
    finally:
        db.close()


# --- 月报口径 ----------------------------------------------------------------

def test_monthly_counts_cash_once(api):
    """充值 100 再用余额付 100：现金只进 100，不是 200。

    这是财务口径最容易错的一处。钱包充值让钱进公司账户，
    余额消费只是这笔钱在系统内部换了形态，再算一次收入就重复了。
    """
    as_root(api)
    order_id = add_order(api, payment_status="paid", status="paid")
    add_payment(api, out_trade_no="PAY-RECHARGE", purpose="wallet_recharge", channel="offline",
                amount_cents=10000)
    add_payment(api, out_trade_no="PAY-SPEND", purpose="order_payment", channel="balance",
                amount_cents=10000, order_id=order_id)

    rows = csv_rows(api.get("/api/admin/finance/exports/monthly",
                            params={"date_from": "2026-11-01", "date_to": "2026-11-30"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["月份"] == "2026-11"
    assert record["现金收款(元)"] == "100.00"
    assert record["现金净额(元)"] == "100.00"
    assert record["余额消费(元)"] == "100.00"


def test_monthly_refund_lands_in_refund_month(api):
    """退款按发生时间归月，不能追溯改写已经出过的上月报表。"""
    as_root(api)
    paid_on_october = datetime(2026, 10, 20, 2, 0, 0)
    payment_id = add_payment(api, out_trade_no="PAY-OCT", amount_cents=50000, paid_at=paid_on_october)

    db = api.session_factory()
    try:
        actor = db.scalar(select(User).where(User.username == "root"))
        db.add(PaymentRefund(payment_order_id=payment_id, amount_cents=50000, channel="offline",
                             status="completed", external_reference="refund-1", reason="客户退单",
                             actor_user_id=actor.id,
                             created_at=local_day_range_utc(datetime(2026, 11, 3).date(),
                                                            datetime(2026, 11, 3).date())[0]))
        db.commit()
    finally:
        db.close()

    rows = csv_rows(api.get("/api/admin/finance/exports/monthly",
                            params={"date_from": "2026-10-01", "date_to": "2026-11-30"}))
    by_month = {row[0]: dict(zip(rows[0], row)) for row in rows[1:]}
    assert by_month["2026-10"]["现金收款(元)"] == "500.00"
    assert by_month["2026-10"]["现金退款(元)"] == "0.00"
    assert by_month["2026-11"]["现金退款(元)"] == "500.00"
    assert by_month["2026-11"]["现金净额(元)"] == "-500.00"


def test_monthly_fills_months_without_activity(api):
    """没有收入的月份也要出行，否则看起来像"这个月报表漏了"。"""
    as_root(api)
    rows = csv_rows(api.get("/api/admin/finance/exports/monthly",
                            params={"date_from": "2026-09-01", "date_to": "2026-11-30"}))
    assert [row[0] for row in rows[1:]] == ["2026-09", "2026-10", "2026-11"]


def test_export_catalog_lists_all_reports(api):
    as_root(api)
    items = api.get("/api/admin/finance/exports").json()
    assert {item["kind"] for item in items} == {"orders", "payments", "wallet", "monthly"}
    # 每张表的归属口径必须写给使用者看，否则四张表的数字对不上时无从解释。
    assert all(item["basis"] for item in items)
