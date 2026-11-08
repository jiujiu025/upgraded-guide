"""历史余额迁移：把 ``customers.balance`` 上的存量余额补进钱包。

背景：余额的权威表示是 ``wallet_accounts.balance_cents``，但历史上存在两类客户：

    - 有 ``customers.balance`` 却没有 ``wallet_accounts`` 行 —— 界面能显示余额，
      但一旦要消费，系统会新建一个 0 余额的钱包并判定「余额不足」；
    - 钱包与浮点副本数值不一致 —— 已经分叉，谁对说不清。

本模块只解决第一类：**为缺失的钱包补建账户，并把存量余额记成一笔期初流水**。
第二类不猜、不覆盖，只报告，交人工判断。

设计要点：

- **幂等**：期初流水的幂等键是 ``opening-balance:{customer_id}``，重复执行不会重复加钱；
- **不覆盖**：钱包已存在且余额不为 0 时一律不写，只报 ``conflict``；
- **可预演**：``plan_balance_migration`` 只读，先出清单再决定是否执行；
- **一人一事务**：某个客户失败不影响其他客户。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, WalletAccount
from app.services.wallet_service import apply_wallet_delta

OPENING_TRANSACTION_TYPE = "opening_balance"

# 计划状态
CREATE_WALLET = "create_wallet"          # 需要补建钱包并记期初
ALREADY_MIGRATED = "already_migrated"    # 已经迁过（幂等重跑）
CONFLICT = "conflict"                    # 钱包已存在但金额不同，需人工判断
NO_ACCOUNT = "no_account"                # 客户没有登录账号，无法建钱包
ZERO = "zero"                            # 余额为 0，无需迁移

ACTIONABLE = (CREATE_WALLET,)
STATUS_LABELS = {
    CREATE_WALLET: "待补建钱包",
    ALREADY_MIGRATED: "已迁移，跳过",
    CONFLICT: "金额冲突，需人工核对",
    NO_ACCOUNT: "无登录账号，无法迁移",
    ZERO: "余额为 0，跳过",
}


@dataclass
class MigrationItem:
    customer_id: int
    name: str
    user_id: int | None
    target_cents: int
    status: str
    detail: str = ""

    @property
    def actionable(self) -> bool:
        return self.status in ACTIONABLE

    def as_dict(self) -> dict:
        return {"customer_id": self.customer_id, "name": self.name, "user_id": self.user_id,
                "target_cents": self.target_cents, "status": self.status,
                "status_label": STATUS_LABELS.get(self.status, self.status), "detail": self.detail}


def plan_balance_migration(db: Session) -> list[MigrationItem]:
    """只读：给出每个有余额客户的迁移计划。"""
    rows = db.execute(
        select(Customer.id, Customer.name, Customer.user_id, Customer.balance, WalletAccount.balance_cents)
        .outerjoin(WalletAccount, WalletAccount.user_id == Customer.user_id)
        .where(Customer.is_deleted.is_(False))
        .order_by(Customer.id)
    ).all()

    items: list[MigrationItem] = []
    for customer_id, name, user_id, balance, wallet_cents in rows:
        target = int(round(float(balance or 0) * 100))
        if target < 0:
            items.append(MigrationItem(customer_id, name, user_id, target, CONFLICT,
                                       "浮点副本为负，无法用期初流水建账，需人工核对"))
            continue
        if target == 0 and wallet_cents is None:
            continue                                    # 没余额也没钱包，与迁移无关
        if wallet_cents is not None:
            wallet = int(wallet_cents)
            if wallet == target:
                items.append(MigrationItem(customer_id, name, user_id, target, ALREADY_MIGRATED,
                                           "钱包余额与浮点副本一致"))
            elif wallet == 0:
                items.append(MigrationItem(customer_id, name, user_id, target, CREATE_WALLET,
                                           "钱包已存在但为空，补记期初"))
            else:
                items.append(MigrationItem(customer_id, name, user_id, target, CONFLICT,
                                           f"钱包已有 {wallet} 分，与浮点副本 {target} 分不一致，"
                                           "不自动改写"))
            continue
        if not user_id:
            items.append(MigrationItem(customer_id, name, user_id, target, NO_ACCOUNT,
                                       "客户没有登录账号，无法建钱包；需业务决定是否补建账号"))
            continue
        items.append(MigrationItem(customer_id, name, user_id, target, CREATE_WALLET))
    return items


def apply_balance_migration(db: Session, item: MigrationItem) -> MigrationItem:
    """执行单个客户的迁移。调用方负责提交/回滚事务。"""
    if not item.actionable:
        return item

    account = db.scalar(select(WalletAccount).where(WalletAccount.user_id == item.user_id))
    if account is None:
        account = WalletAccount(user_id=item.user_id, balance_cents=0, currency="CNY")
        db.add(account)
        db.flush()

    if int(account.balance_cents) != 0:
        item.status = CONFLICT
        item.detail = f"钱包已有 {int(account.balance_cents)} 分，未写入"
        return item

    apply_wallet_delta(
        db, item.user_id, item.target_cents, OPENING_TRANSACTION_TYPE,
        f"opening-balance:{item.customer_id}",
        remark="历史余额迁移：原 customers.balance 存量转入钱包",
    )
    db.flush()

    if int(account.balance_cents) != item.target_cents:
        raise ValueError(f"客户 #{item.customer_id} 迁移后余额 {account.balance_cents} ≠ 目标 {item.target_cents}")

    item.status = ALREADY_MIGRATED
    item.detail = f"已写入 {item.target_cents} 分"
    return item
