"""历史余额迁移的幂等与安全性验证。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Customer, User, WalletAccount, WalletTransaction
from app.models.base import Base
from app.services.wallet_audit_service import audit_wallet_ledger
from app.services.wallet_migration_service import (
    ALREADY_MIGRATED, CONFLICT, CREATE_WALLET, NO_ACCOUNT,
    apply_balance_migration, plan_balance_migration,
)


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()
    engine.dispose()


def _customer_with_account(db, name: str, balance: float | None) -> Customer:
    user = User(username=f"u_{name}", password_hash="x", role="customer", display_name=name)
    db.add(user)
    db.flush()
    customer = Customer(name=name, user_id=user.id, balance=balance)
    db.add(customer)
    db.commit()
    return customer


def _status_of(plan, customer_id):
    return next(item for item in plan if item.customer_id == customer_id)


def test_plan_targets_orphan_balance(env):
    customer = _customer_with_account(env, "孤儿", 88.88)

    plan = plan_balance_migration(env)

    item = _status_of(plan, customer.id)
    assert item.status == CREATE_WALLET
    assert item.target_cents == 8888


def test_apply_creates_wallet_with_opening_transaction(env):
    customer = _customer_with_account(env, "迁移后", 88.88)
    item = _status_of(plan_balance_migration(env), customer.id)

    apply_balance_migration(env, item)
    env.commit()

    account = env.query(WalletAccount).filter_by(user_id=customer.user_id).one()
    assert account.balance_cents == 8888
    transaction = env.query(WalletTransaction).filter_by(idempotency_key=f"opening-balance:{customer.id}").one()
    assert transaction.transaction_type == "opening_balance"
    assert transaction.amount_cents == 8888
    assert transaction.balance_before_cents == 0
    assert transaction.balance_after_cents == 8888
    # 迁移不能破坏账本自洽
    assert audit_wallet_ledger(env).ok


def test_apply_is_idempotent(env):
    customer = _customer_with_account(env, "重跑", 50.00)

    for _ in range(2):
        plan = plan_balance_migration(env)
        item = _status_of(plan, customer.id)
        if item.status == CREATE_WALLET:
            apply_balance_migration(env, item)
            env.commit()

    account = env.query(WalletAccount).filter_by(user_id=customer.user_id).one()
    assert account.balance_cents == 5000
    assert env.query(WalletTransaction).filter_by(wallet_account_id=account.id).count() == 1


def test_existing_matching_wallet_is_skipped(env):
    customer = _customer_with_account(env, "已一致", 100.00)
    env.add(WalletAccount(user_id=customer.user_id, balance_cents=10000, currency="CNY"))
    env.commit()

    item = _status_of(plan_balance_migration(env), customer.id)

    assert item.status == ALREADY_MIGRATED


def test_conflicting_wallet_is_reported_not_overwritten(env):
    customer = _customer_with_account(env, "冲突", 100.00)
    env.add(WalletAccount(user_id=customer.user_id, balance_cents=5000, currency="CNY"))
    env.commit()

    item = _status_of(plan_balance_migration(env), customer.id)
    assert item.status == CONFLICT

    apply_balance_migration(env, item)          # 不可执行项应原样返回
    env.commit()

    account = env.query(WalletAccount).filter_by(user_id=customer.user_id).one()
    assert account.balance_cents == 5000        # 不被浮点副本覆盖


def test_empty_existing_wallet_gets_opening_entry(env):
    customer = _customer_with_account(env, "空钱包", 20.00)
    env.add(WalletAccount(user_id=customer.user_id, balance_cents=0, currency="CNY"))
    env.commit()

    item = _status_of(plan_balance_migration(env), customer.id)
    assert item.status == CREATE_WALLET

    apply_balance_migration(env, item)
    env.commit()

    account = env.query(WalletAccount).filter_by(user_id=customer.user_id).one()
    assert account.balance_cents == 2000


def test_customer_without_login_account_is_skipped(env):
    customer = Customer(name="无账号", user_id=None, balance=66.66)
    env.add(customer)
    env.commit()

    item = _status_of(plan_balance_migration(env), customer.id)

    assert item.status == NO_ACCOUNT


def test_negative_balance_is_not_attempted(env):
    customer = _customer_with_account(env, "负余额", -1.00)

    item = _status_of(plan_balance_migration(env), customer.id)

    assert item.status == CONFLICT
    assert "负" in item.detail


def test_zero_balance_without_wallet_is_ignored(env):
    customer = _customer_with_account(env, "零余额", 0.0)

    plan = plan_balance_migration(env)

    assert all(item.customer_id != customer.id for item in plan)
