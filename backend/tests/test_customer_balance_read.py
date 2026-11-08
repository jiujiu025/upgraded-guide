"""余额读口径：客户相关接口必须读钱包，不能回退 customers.balance 浮点副本。

背景见 docs/金额一致性现状与迁移方案.md Step 1。核心诉求是三个页面说同一个数：
`/wallet`、客户列表 / 客户 360、客户自助页。历史实现里后两者读的是浮点副本，
于是"没有钱包的客户"会在列表显示一个花不出去的余额，而钱包页显示 0。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User, WalletAccount
from app.models.base import Base
from app.services.wallet_service import apply_wallet_delta


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    customer_user = User(username="customer_a", password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户 A")
    db.add_all([admin, sales, customer_user])
    db.flush()

    # 有钱包：浮点副本被改坏成 99.99，钱包里是 10000 分，读接口必须以钱包为准
    with_wallet = Customer(name="有钱包", email="w@example.com", user_id=customer_user.id,
                           sales_id=sales.id, balance=99.99)
    # 没有钱包：浮点副本有 50 元，但没有 wallet_accounts 行，读接口必须显示 0
    without_wallet = Customer(name="没钱包", email="n@example.com", sales_id=sales.id, balance=50)
    db.add_all([with_wallet, without_wallet])
    db.flush()
    db.add(WalletAccount(user_id=customer_user.id, balance_cents=10000, currency="CNY"))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.db = db
    client.ids = {"with_wallet": with_wallet.id, "without_wallet": without_wallet.id}
    yield client

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _auth(api, username, password):
    response = api.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _by_name(items):
    return {item["name"]: item for item in items}


def test_customer_list_reads_wallet_not_float_copy(api):
    headers = _auth(api, "admin", "Admin_123456")
    response = api.get("/api/customers", headers=headers)
    assert response.status_code == 200, response.text
    rows = _by_name(response.json())
    # 浮点副本是 99.99，钱包是 100.00
    assert rows["有钱包"]["balance"] == 100.0
    # 没有钱包就是 0，不再显示那 50 元
    assert rows["没钱包"]["balance"] == 0.0


def test_customer_detail_reads_wallet_not_float_copy(api):
    headers = _auth(api, "admin", "Admin_123456")
    response = api.get(f"/api/customers/{api.ids['with_wallet']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["balance"] == 100.0


def test_sales_customer_list_reads_wallet(api):
    headers = _auth(api, "sales_a", "Sales_123456")
    response = api.get("/api/sales/customers", headers=headers)
    assert response.status_code == 200, response.text
    rows = _by_name(response.json())
    assert rows["有钱包"]["balance"] == 100.0
    assert rows["没钱包"]["balance"] == 0.0


def test_customer_me_reads_wallet(api):
    headers = _auth(api, "customer_a", "Customer_123456")
    response = api.get("/api/customer/me", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["balance"] == 100.0


def test_customer_list_matches_wallet_page(api):
    """同一个客户，客户列表与 /wallet 必须给出同一个数。"""
    customer_headers = _auth(api, "customer_a", "Customer_123456")
    wallet_cents = api.get("/api/wallet", headers=customer_headers).json()["balance_cents"]
    admin_headers = _auth(api, "admin", "Admin_123456")
    listed = _by_name(api.get("/api/customers", headers=admin_headers).json())["有钱包"]["balance"]
    assert round(listed * 100) == wallet_cents


def test_admin_customer_detail_zero_without_wallet(api):
    """管理端客户 360 同样不给无钱包客户编造余额。"""
    headers = _auth(api, "admin", "Admin_123456")
    response = api.get(f"/api/admin/customers/{api.ids['without_wallet']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["customer"]["balance_cents"] == 0


def test_admin_customer_list_zero_without_wallet(api):
    headers = _auth(api, "admin", "Admin_123456")
    response = api.get("/api/admin/customers", headers=headers)
    assert response.status_code == 200, response.text
    rows = {item["name"]: item for item in response.json()["items"]}
    assert rows["没钱包"]["balance_cents"] == 0
    assert rows["有钱包"]["balance_cents"] == 10000


def test_float_copy_write_stays_exact(api):
    """Step 2：副本仍照旧写，但写进去的值必须能原样还原成整数分。"""
    db = api.db
    customer = db.query(Customer).filter_by(name="有钱包").one()
    apply_wallet_delta(db, customer.user_id, 2345, "offline_recharge", "copy-check-1")
    db.commit()
    db.refresh(customer)
    assert customer.balance == 123.45
    assert int(round(customer.balance * 100)) == 12345
