"""客户对外视图：余额一律取钱包权威值，不回退 ``customers.balance``。

为什么单独一层：``CustomerOut.balance`` 是**展示字段**，但历史实现直接由
``Customer.balance`` 这个可写浮点副本填充。于是出现两种口径——

- ``/wallet`` 与财务页：读 ``wallet_accounts.balance_cents``，没有钱包就是 0；
- 客户列表 / 客户 360：读浮点副本，没有钱包却可能显示一个花不出去的余额。

同一笔钱在两个页面各说各话，用户没法判断哪个是真的。这里把展示口径统一到
钱包：**没有钱包账户即余额为 0**，与 ``/wallet`` 完全一致。

注意：本模块只做"取值 + 组装"，不创建钱包、不改数据。历史余额要先由
``scripts/migrate_customer_balance_to_wallet.py`` 补进钱包，再让读口径切过来，
顺序反了会让那部分客户的余额凭空消失。
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, WalletAccount
from app.schemas.common import CustomerOut


def balance_cents_of(db: Session, user_id: int | None) -> int:
    """取权威余额（整数分）。没有账号或没有钱包账户都返回 0。"""
    if not user_id:
        return 0
    cents = db.scalar(select(WalletAccount.balance_cents).where(WalletAccount.user_id == user_id))
    return int(cents or 0)


def wallet_cents_map(db: Session, user_ids: Iterable[int | None]) -> dict[int, int]:
    """批量取权威余额，键是 ``user_id``，避免列表接口逐条查询（N+1）。"""
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    rows = db.execute(
        select(WalletAccount.user_id, WalletAccount.balance_cents).where(WalletAccount.user_id.in_(ids))
    ).all()
    return {int(uid): int(cents or 0) for uid, cents in rows}


def customer_view(customer: Customer, balance_cents: int) -> CustomerOut:
    """按钱包余额组装对外视图。字段名与类型保持 ``balance: float``，前端无需改动。"""
    view = CustomerOut.model_validate(customer)
    return view.model_copy(update={"balance": balance_cents / 100, "has_account": customer.user_id is not None})


def customer_views(db: Session, customers: Iterable[Customer]) -> list[CustomerOut]:
    """列表版：一次查询取出全部钱包余额，再逐个组装。"""
    items = list(customers)
    cents_map = wallet_cents_map(db, (item.user_id for item in items))
    return [customer_view(item, cents_map.get(item.user_id, 0) if item.user_id else 0) for item in items]
