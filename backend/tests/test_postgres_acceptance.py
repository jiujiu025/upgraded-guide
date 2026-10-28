import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Customer, CustomerLine, Node, Order, Product, Region, User
from app.models.base import utcnow
from app.services.order_service import claim_delivery_slot


pytestmark = pytest.mark.postgres
POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@pytest.fixture(scope="module")
def postgres_session_factory():
    if not POSTGRES_URL:
        pytest.skip("仅在显式配置 TEST_POSTGRES_URL 的隔离环境运行")
    engine = create_engine(POSTGRES_URL, pool_size=8, max_overflow=4)
    with engine.begin() as connection:
        connection.execute(text(
            "TRUNCATE TABLE inbox_messages, inbox_threads, audit_logs, notifications, "
            "health_incidents, customer_lines, orders, nodes, products, customers, regions, users "
            "RESTART IDENTITY CASCADE"
        ))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with engine.begin() as connection:
        connection.execute(text(
            "TRUNCATE TABLE inbox_messages, inbox_threads, audit_logs, notifications, "
            "health_incidents, customer_lines, orders, nodes, products, customers, regions, users "
            "RESTART IDENTITY CASCADE"
        ))
    engine.dispose()


def create_delivery_order(session_factory, order_no: str, quantity: int):
    with session_factory() as db:
        region = Region(name=f"验收地区-{order_no}", country_code="AC")
        user = User(username=f"user-{order_no}", password_hash="unused", role="customer", display_name="验收客户")
        db.add_all([region, user])
        db.flush()
        customer = Customer(name="验收客户", email=f"{order_no}@example.com", user_id=user.id)
        product = Product(name=f"验收产品-{order_no}", product_type="线路", price=100, billing_days=30, region_id=region.id)
        node = Node(name=f"验收节点-{order_no}", server="192.0.2.1", port=443, protocol="VLESS", region_id=region.id)
        db.add_all([customer, product, node])
        db.flush()
        order = Order(
            order_no=order_no,
            customer_id=customer.id,
            product_id=product.id,
            amount=100 * quantity,
            quantity=quantity,
            status="pending_delivery",
        )
        db.add(order)
        db.commit()
        return order.id, customer.id, product.id, node.id


def test_postgresql_concurrent_delivery_never_exceeds_quantity(postgres_session_factory):
    order_id, customer_id, product_id, node_id = create_delivery_order(postgres_session_factory, "PG-CONCURRENT", 2)
    barrier = Barrier(4)

    def deliver_once() -> bool:
        with postgres_session_factory() as db:
            barrier.wait()
            claimed = claim_delivery_slot(db, order_id, utcnow())
            if claimed:
                db.add(CustomerLine(
                    customer_id=customer_id,
                    product_id=product_id,
                    node_id=node_id,
                    order_id=order_id,
                    started_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=30),
                ))
            db.commit()
            return claimed

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: deliver_once(), range(4)))

    with postgres_session_factory() as db:
        order = db.get(Order, order_id)
        line_count = db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.order_id == order_id))
        assert sum(results) == 2
        assert order.delivered_quantity == 2
        assert order.status == "delivered"
        assert line_count == 2


def test_postgresql_delivery_transaction_rolls_back_as_a_unit(postgres_session_factory):
    order_id, customer_id, product_id, node_id = create_delivery_order(postgres_session_factory, "PG-ROLLBACK", 1)

    with postgres_session_factory() as db:
        assert claim_delivery_slot(db, order_id, utcnow())
        db.add(CustomerLine(
            customer_id=customer_id,
            product_id=product_id,
            node_id=node_id,
            order_id=order_id,
            started_at=utcnow(),
            expires_at=utcnow() + timedelta(days=30),
        ))
        db.flush()
        db.rollback()

    with postgres_session_factory() as db:
        order = db.get(Order, order_id)
        line_count = db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.order_id == order_id))
        assert order.delivered_quantity == 0
        assert order.status == "pending_delivery"
        assert line_count == 0
