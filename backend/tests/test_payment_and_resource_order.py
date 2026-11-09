"""余额支付与资源池下单的回归用例。

两条链路此前都没有测试覆盖，因此各自藏着一个必现的 500：

1. 余额支付把 ``User`` 对象当成 ``actor_user_id`` 传给佣金记账，SQLite/PostgreSQL
   在绑定参数时直接报错 —— 只要客户有归属销售，余额支付就 100% 失败；
2. 资源池下单的审计文案引用了未定义的 ``retail_rule``，NameError 被外层
   ``except Exception`` 吞成 500 —— 整个「订单中心 → 新建资源池订单」不可用。

这里直接调用路由函数（不经过 HTTP），把「能不能真正落库」钉住。
"""
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.finance_routes import IdempotencyIn, pay_order_with_balance
from app.api.routes import AdminResourceOrderIn, admin_create_resource_order
from app.core.security import hash_password
from app.models import (
    AuditLog, Customer, Order, Product, Region, RetailPriceRule, SalesCommissionEntry,
    Supplier, SupplierIpSegmentSnapshot, SupplierProduct, SupplierQuote, SupplierRegion,
    SupplierSyncRun, User, WalletAccount,
)
from app.models.base import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _base_records(db):
    region = Region(name="美国", country_code="US")
    db.add(region)
    db.flush()
    product = Product(
        name="美国原生住宅 30天", product_type="static_residential_native",
        price=60, billing_days=30, region_id=region.id,
        resource_category="static_residential_native",
    )
    sales = User(username="sales1", password_hash=hash_password("Sales_123456"),
                 role="sales", display_name="销售A")
    admin = User(username="admin1", password_hash=hash_password("Admin_123456"),
                 role="admin", display_name="管理员")
    customer_user = User(username="cust1", password_hash=hash_password("Cust_1234567"),
                         role="customer", display_name="客户A")
    db.add_all([product, sales, admin, customer_user])
    db.flush()
    customer = Customer(name="客户A", email="a@example.com",
                        user_id=customer_user.id, sales_id=sales.id)
    db.add(customer)
    db.flush()
    return region, product, sales, admin, customer_user, customer


def test_balance_payment_succeeds_and_records_commission_actor(db):
    _, product, sales, _, customer_user, customer = _base_records(db)
    db.add(WalletAccount(user_id=customer_user.id, balance_cents=100000, currency="CNY"))
    order = Order(
        order_no="ORD-BALANCE-1", customer_id=customer.id, product_id=product.id,
        amount=100, quantity=1, billing_days=30, unit_price=100, total_amount=100,
        region_name="美国", product_name=product.name, payment_method="online",
        payment_status="unpaid", status="pending_payment", order_type="purchase",
        assigned_sales_id=sales.id,
    )
    db.add(order)
    db.commit()

    result = pay_order_with_balance(
        order.id, IdempotencyIn(idempotency_key="k" * 20), db=db, user=customer_user,
    )

    assert result["status"] == "paid"
    db.refresh(order)
    assert order.status == "paid" and order.payment_status == "paid"
    account = db.scalar(select(WalletAccount).where(WalletAccount.user_id == customer_user.id))
    assert account.balance_cents == 90000

    entry = db.scalar(select(SalesCommissionEntry).where(
        SalesCommissionEntry.idempotency_key == f"order-paid:{order.id}",
    ))
    assert entry is not None
    assert entry.amount_cents == 1500
    # 付款人是客户本人；此前这里传的是 User 对象，绑定参数直接报错。
    assert entry.actor_user_id == customer_user.id


def test_resource_pool_order_creation_succeeds(db):
    region, product, _, admin, _, customer = _base_records(db)
    supplier = Supplier(code="gp", name="GlobalProxy", adapter_type="global_proxy",
                        base_url="https://example.invalid", secret_ref="vault:gp")
    db.add(supplier)
    db.flush()
    run = SupplierSyncRun(supplier_id=supplier.id, actor_id=admin.id,
                          sync_type="inventory", started_at=datetime.utcnow())
    db.add(run)
    db.flush()
    supplier_product = SupplierProduct(
        supplier_id=supplier.id, external_product_ref="p1", name_snapshot="原生住宅",
        resource_category="static_residential_native", local_product_id=product.id,
        currency="CNY", is_available=True, source_endpoint="/products",
        last_synced_at=datetime.utcnow(),
    )
    db.add(supplier_product)
    db.flush()
    supplier_region = SupplierRegion(
        supplier_product_id=supplier_product.id, external_country_ref="1",
        country_code="US", country_name="美国", external_city_ref="", city_name=None,
        local_region_id=region.id, is_available=True, source_endpoint="/regions",
        last_synced_at=datetime.utcnow(),
    )
    db.add(supplier_region)
    db.flush()
    segment = SupplierIpSegmentSnapshot(
        supplier_id=supplier.id, supplier_product_id=supplier_product.id,
        supplier_region_id=supplier_region.id, sync_run_id=run.id, country_code="US",
        cidr="1.1.1.0/24", requested_quantity=10, is_segment_sufficient=True,
        is_request_sufficient=True, checked_at=datetime.utcnow(),
    )
    quote = SupplierQuote(
        supplier_id=supplier.id, supplier_product_id=supplier_product.id,
        external_product_ref="p1", country_code="US", duration_days=180, unit_cost=10,
        currency="CNY", is_available=True, source_endpoint="/quotes",
        quoted_at=datetime.utcnow(),
    )
    rule = RetailPriceRule(
        resource_category="static_residential_native", country_code="US",
        billing_days=30, unit_price=60, currency="CNY", source_version="v1",
    )
    db.add_all([segment, quote, rule])
    db.commit()

    payload = AdminResourceOrderIn(
        customer_id=customer.id, product_id=product.id,
        supplier_product_id=supplier_product.id,
        supplier_region_id=supplier_region.id,
        supplier_segment_snapshot_id=segment.id, supplier_quote_id=quote.id,
        idempotency_key=str(uuid4()),
        resource_category="static_residential_native", quantity=2, billing_days=180,
    )
    result = admin_create_resource_order(payload, db=db, user=admin)

    assert result["order_no"].startswith("ORD-")
    assert result["total_amount"] == 720.0     # 60 × 6 × 2，线性无折扣
    assert result["status"] == "pending_payment"

    order = db.scalar(select(Order).where(Order.order_no == result["order_no"]))
    assert order is not None and order.source_type == "resource_pool"
    # 审计必须能指出售价来自哪一版价目表，而不是抛 NameError 把整单回滚。
    audit = db.scalar(
        select(AuditLog)
        .where(AuditLog.resource_type == "order", AuditLog.resource_id == str(order.id))
        .order_by(AuditLog.id.desc())
    )
    assert audit is not None and "v1" in (audit.detail or "")
