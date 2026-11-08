"""钱包账本一致性巡检（只读，不改任何数据）。

系统里同一笔余额有三处表示，本模块负责核对它们是否互相自洽：

    1. ``wallet_accounts.balance_cents``  —— 权威余额，整数分
    2. ``wallet_transactions``            —— 明细账，累计金额应等于 1
    3. ``customers.balance``              —— 历史冗余副本，浮点元，应等于 1/100

检查项分为两类：

**阻断（blocking）** —— 说明账本身错了，必须先查清才能迁移金额类型
    - ``balance_vs_ledger``            账户余额 ≠ 该账户所有流水之和
    - ``ledger_chain``                 相邻流水首尾不接（before ≠ 上一条 after），或单条 before+amount ≠ after
    - ``missing_wallet_transaction``   已付款的支付单在钱包里没有对应流水（钱收了但没入账 / 扣了但没记录）
    - ``missing_refund_transaction``   余额渠道的退款登记没有对应的钱包退回流水
    - ``negative_balance``             账户余额为负（违反约束，通常意味着绕过 ORM 直接改库）

**提示（non-blocking）** —— 迁移前需要人工确认，但不是账错
    - ``customer_float_drift``         冗余浮点副本与权威余额不一致（双真相源已经开始分叉）
    - ``orphan_customer_balance``      客户有余额但没有任何钱包账户（历史数据，尚未迁移到钱包）
    - ``ledger_opening``               账户最早一条流水的期初余额不为 0（有余额没有来源流水）

用法::

    from app.services.wallet_audit_service import audit_wallet_ledger
    report = audit_wallet_ledger(db)
    report.blocking_issues  # list[Issue]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, PaymentOrder, PaymentRefund, WalletAccount, WalletTransaction

# 支付单在这些状态下应当能在钱包里找到对应流水
_WALLET_BACKED_PAYMENT_STATUSES = ("paid", "refunded")


@dataclass(frozen=True)
class Issue:
    """一条巡检发现。``kind`` 用于程序判定，``detail`` 给人看。"""

    kind: str
    subject: str
    detail: str
    blocking: bool

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject,
                "detail": self.detail, "blocking": self.blocking}


@dataclass
class AuditReport:
    accounts: int = 0
    transactions: int = 0
    customers: int = 0
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def blocking_issues(self) -> list[Issue]:
        return [item for item in self.issues if item.blocking]

    @property
    def warnings(self) -> list[Issue]:
        return [item for item in self.issues if not item.blocking]

    @property
    def ok(self) -> bool:
        return not self.blocking_issues

    def count(self, kind: str) -> int:
        return sum(1 for item in self.issues if item.kind == kind)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "accounts": self.accounts,
            "transactions": self.transactions,
            "customers": self.customers,
            "blocking": len(self.blocking_issues),
            "warnings": len(self.warnings),
            "stats": dict(self.stats),
            "issues": [item.as_dict() for item in self.issues],
        }


def _yuan(cents: int) -> str:
    return f"{Decimal(int(cents)) / Decimal(100):.2f}"


def audit_wallet_ledger(db: Session, *, detail_limit: int = 50) -> AuditReport:
    """只读巡检。``detail_limit`` 只限制每条问题的明细条数，不影响计数。"""
    report = AuditReport()
    _check_account_balance_vs_ledger(db, report, detail_limit)
    _check_ledger_chain(db, report, detail_limit)
    _check_payment_linkage(db, report, detail_limit)
    _check_refund_linkage(db, report, detail_limit)
    _check_customer_float_copy(db, report, detail_limit)
    return report


def _check_account_balance_vs_ledger(db: Session, report: AuditReport, detail_limit: int) -> None:
    sums = {
        account_id: int(total or 0)
        for account_id, total in db.execute(
            select(WalletTransaction.wallet_account_id, func.sum(WalletTransaction.amount_cents))
            .group_by(WalletTransaction.wallet_account_id)
        ).all()
    }
    accounts = db.scalars(select(WalletAccount)).all()
    report.accounts = len(accounts)
    report.transactions = int(db.scalar(select(func.count()).select_from(WalletTransaction)) or 0)
    mismatches = 0
    for account in accounts:
        ledger_total = sums.get(account.id, 0)
        balance = int(account.balance_cents)
        if balance < 0:
            _add(report, "negative_balance", f"wallet#{account.id}",
                 f"用户 #{account.user_id} 余额 {_yuan(balance)} 为负", blocking=True, detail_limit=detail_limit)
        if balance != ledger_total:
            mismatches += 1
            _add(report, "balance_vs_ledger", f"wallet#{account.id}",
                 f"用户 #{account.user_id} 账户余额 {_yuan(balance)}，流水累计 {_yuan(ledger_total)}，"
                 f"差 {_yuan(balance - ledger_total)}", blocking=True, detail_limit=detail_limit)
    report.stats["balance_vs_ledger"] = mismatches


def _check_ledger_chain(db: Session, report: AuditReport, detail_limit: int) -> None:
    rows = db.execute(
        select(
            WalletTransaction.wallet_account_id, WalletTransaction.id, WalletTransaction.transaction_type,
            WalletTransaction.amount_cents, WalletTransaction.balance_before_cents, WalletTransaction.balance_after_cents,
        ).order_by(WalletTransaction.wallet_account_id, WalletTransaction.created_at, WalletTransaction.id)
    ).all()
    previous: dict[int, tuple[int, int]] = {}   # account_id -> (tx_id, balance_after)
    opened: set[int] = set()
    broken = 0
    for account_id, tx_id, tx_type, amount, before, after in rows:
        before, after, amount = int(before), int(after), int(amount)
        if before + amount != after:
            broken += 1
            _add(report, "ledger_chain", f"tx#{tx_id}",
                 f"流水自身不平：期初 {_yuan(before)} + 金额 {_yuan(amount)} ≠ 期末 {_yuan(after)}"
                 f"（类型 {tx_type}）", blocking=True, detail_limit=detail_limit)
        if account_id not in opened:
            opened.add(account_id)
            if before != 0:
                _add(report, "ledger_opening", f"wallet#{account_id}",
                     f"最早一条流水（tx#{tx_id}）期初余额为 {_yuan(before)}，"
                     "说明有余额没有来源流水，迁移前需人工确认", blocking=False, detail_limit=detail_limit)
        else:
            prev_id, prev_after = previous[account_id]
            if before != prev_after:
                broken += 1
                _add(report, "ledger_chain", f"tx#{tx_id}",
                     f"与上一条 tx#{prev_id} 不接：上条期末 {_yuan(prev_after)}，本条期初 {_yuan(before)}",
                     blocking=True, detail_limit=detail_limit)
        previous[account_id] = (tx_id, after)
    report.stats["ledger_chain"] = broken


def _check_payment_linkage(db: Session, report: AuditReport, detail_limit: int) -> None:
    payments = db.scalars(select(PaymentOrder).where(
        PaymentOrder.status.in_(_WALLET_BACKED_PAYMENT_STATUSES),
    )).all()
    # 按 (支付单, 流水类型) 分组：同一张支付单可能同时有扣款（order_payment）和
    # 退回（order_refund），混在一起求和会得到 0，从而被误判为"扣款丢失"。
    totals: dict[tuple[int, str], int] = {}
    for payment_order_id, tx_type, total in db.execute(
        select(
            WalletTransaction.payment_order_id,
            WalletTransaction.transaction_type,
            func.sum(WalletTransaction.amount_cents),
        ).where(WalletTransaction.payment_order_id.is_not(None))
        .group_by(WalletTransaction.payment_order_id, WalletTransaction.transaction_type)
    ).all():
        totals[(int(payment_order_id), tx_type)] = int(total or 0)

    missing = 0
    for payment in payments:
        pid = int(payment.id)
        if payment.purpose == "order_payment":
            if payment.channel != "balance":
                continue                # 在线/线下收款不进钱包
            expected = -int(payment.amount_cents)
            actual = totals.get((pid, "order_payment"))
            hint = "余额支付应在钱包扣款"
        elif payment.purpose == "wallet_recharge":
            expected = int(payment.amount_cents)
            offline, online = totals.get((pid, "offline_recharge")), totals.get((pid, "online_recharge"))
            actual = None if offline is None and online is None else int(offline or 0) + int(online or 0)
            hint = "充值到账应在钱包入账"
        else:
            continue
        if actual is None or actual != expected:
            missing += 1
            shown = "无流水" if actual is None else _yuan(actual)
            _add(report, "missing_wallet_transaction", f"payment#{payment.id}",
                 f"{payment.out_trade_no}（{payment.purpose}/{payment.channel}，{payment.status}）"
                 f"金额 {_yuan(payment.amount_cents)}，{hint}：期望 {_yuan(expected)}，实际 {shown}",
                 blocking=True, detail_limit=detail_limit)
    report.stats["missing_wallet_transaction"] = missing


def _check_refund_linkage(db: Session, report: AuditReport, detail_limit: int) -> None:
    refunds = db.scalars(select(PaymentRefund).where(PaymentRefund.channel == "balance")).all()
    if not refunds:
        report.stats["missing_refund_transaction"] = 0
        return
    refunded = {
        order_id: int(total or 0)
        for order_id, total in db.execute(
            select(WalletTransaction.order_id, func.sum(WalletTransaction.amount_cents))
            .where(WalletTransaction.transaction_type == "order_refund", WalletTransaction.order_id.is_not(None))
            .group_by(WalletTransaction.order_id)
        ).all()
    }
    missing = 0
    for refund in refunds:
        payment = db.get(PaymentOrder, refund.payment_order_id)
        if not payment:
            continue
        actual = refunded.get(payment.order_id)
        if actual is None or actual != int(refund.amount_cents):
            missing += 1
            shown = "无退回流水" if actual is None else _yuan(actual)
            _add(report, "missing_refund_transaction", f"refund#{refund.id}",
                 f"支付单 #{payment.id} 余额渠道退款 {_yuan(refund.amount_cents)}，钱包应退回同额：实际 {shown}",
                 blocking=True, detail_limit=detail_limit)
    report.stats["missing_refund_transaction"] = missing


def _check_customer_float_copy(db: Session, report: AuditReport, detail_limit: int) -> None:
    rows = db.execute(
        select(Customer.id, Customer.name, Customer.balance, Customer.user_id, WalletAccount.balance_cents)
        .outerjoin(WalletAccount, WalletAccount.user_id == Customer.user_id)
        .where(Customer.is_deleted.is_(False))
        .order_by(Customer.id)
    ).all()
    report.customers = len(rows)
    drift = orphans = 0
    for customer_id, name, balance, user_id, wallet_cents in rows:
        float_cents = int(round(float(balance or 0) * 100))
        if wallet_cents is None:
            if float_cents != 0:
                orphans += 1
                _add(report, "orphan_customer_balance", f"customer#{customer_id}",
                     f"{name} 冗余余额 {_yuan(float_cents)}，但没有钱包账户"
                     f"（user_id={user_id}），需在迁移时补建钱包", blocking=False, detail_limit=detail_limit)
            continue
        if float_cents != int(wallet_cents):
            drift += 1
            _add(report, "customer_float_drift", f"customer#{customer_id}",
                 f"{name} 浮点副本 {_yuan(float_cents)} ≠ 钱包权威余额 {_yuan(int(wallet_cents))}，"
                 f"差 {_yuan(float_cents - int(wallet_cents))}", blocking=False, detail_limit=detail_limit)
    report.stats["customer_float_drift"] = drift
    report.stats["orphan_customer_balance"] = orphans


def _add(report: AuditReport, kind: str, subject: str, detail: str, *, blocking: bool, detail_limit: int) -> None:
    """同一条问题只保留 ``detail_limit`` 条明细，但计数仍以 stats 为准。"""
    if sum(1 for item in report.issues if item.kind == kind) >= detail_limit:
        return
    report.issues.append(Issue(kind=kind, subject=subject, detail=detail, blocking=blocking))
