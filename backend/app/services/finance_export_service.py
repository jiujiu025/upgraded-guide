"""财务导出：把账目导成财务能直接拿去用的 CSV。

三条口径约束，写在这里是因为它们决定数字对不对得上：

1. **时区**：库里存 naive UTC，报表按上海自然日切分，统一走 `app.core.timeutil`。
2. **金额**：钱包与支付单以「分」为单位存整数，导出成「元」两位小数，
   用 `Decimal` 转换而不是除 100 的浮点，避免 0.1+0.2 式的尾差。
3. **口径不同不要混**：订单明细按**下单时间**归属，收款明细按**支付时间**归属，
   钱包流水按**记账时间**归属，月报按**现金**口径。同一笔业务在这四张表里出现的位置
   不同，加起来也不会相等——这是记账规则，不是 bug，所以每张表都在文档里写明口径。

CSV 细节：带 UTF-8 BOM（否则 Excel 打开中文乱码）、CRLF 换行（Excel 默认）、
文本列做公式注入防护（`=`/`+`/`-`/`@` 开头的客户名在 Excel 里会被当公式执行）。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import (
    local_date_of, local_day_range_utc, local_month_of, local_stamp, month_keys,
)
from app.models import (
    Customer, Order, PaymentOrder, PaymentRefund, SalesCommissionEntry, User,
    WalletAccount, WalletTransaction,
)
from app.models.base import utcnow

# 单次导出允许的最大跨度。财务按月导是常态，一年足够；再长就该拆成多次，
# 否则一次请求要扫全表，还会拖住导出期间的数据库连接。
MAX_RANGE_DAYS = 366

KIND_TITLES = {
    "orders": "订单明细",
    "payments": "收款明细",
    "wallet": "钱包流水",
    "monthly": "月度汇总",
}

# 文本与枚举的中文映射。导出的表是给人看的，英文状态码财务看不懂；
# 遇到映射表里没有的新值就原样输出，不要静默丢成「未知」——那样反而查不到问题。
ORDER_STATUS = {
    "pending_confirm": "待确认",
    "pending_payment": "待付款",
    "paid": "已付款",
    "processing": "交付中",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunded": "已退款",
}
PAYMENT_STATUS = {"unpaid": "未支付", "paid": "已支付", "partially_refunded": "部分退款", "refunded": "已退款"}
PAYMENT_PURPOSE = {"wallet_recharge": "钱包充值", "order_payment": "订单支付"}
PAYMENT_CHANNEL = {"online": "线上支付", "offline": "线下收款", "balance": "余额支付"}
PAYMENT_STATE = {
    "pending": "待支付", "paid": "已支付", "refunded": "已退款",
    "expired": "已过期", "cancelled": "已取消", "manual_review": "人工核查", "failed": "支付失败",
}
WALLET_TYPE = {
    "offline_recharge": "线下充值",
    "online_recharge": "线上充值",
    "order_payment": "订单消费",
    "order_refund": "订单退款",
    "opening_balance": "期初余额",
}
ROLE_LABEL = {"customer": "客户", "sales": "销售", "admin": "管理员", "super_admin": "超级管理员"}

# 以这些字符开头的文本在 Excel / WPS 里会被当成公式。客户名和备注都是用户输入，
# 一条 `=HYPERLINK(...)` 的备注就足以在财务的机器上执行。加前导单引号即可，
# Excel 会隐藏这个引号、只显示原文。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class Column:
    """一列的定义。kind 决定单元格怎么格式化，title 直接成为 CSV 表头。"""

    title: str
    kind: str  # text | money | cents | int | datetime


ORDER_COLUMNS = (
    Column("订单号", "text"),
    Column("下单时间", "datetime"),
    Column("支付时间", "datetime"),
    Column("客户", "text"),
    Column("销售", "text"),
    Column("产品", "text"),
    Column("地区", "text"),
    Column("资源分类", "text"),
    Column("数量", "int"),
    Column("周期(天)", "int"),
    Column("单价(元)", "money"),
    Column("应收金额(元)", "money"),
    Column("实收金额(元)", "money"),
    Column("支付状态", "text"),
    Column("订单状态", "text"),
    Column("供应商成本(元)", "money"),
    Column("毛利(元)", "money"),
)

PAYMENT_COLUMNS = (
    Column("支付时间", "datetime"),
    Column("商户单号", "text"),
    Column("平台流水号", "text"),
    Column("付款账户", "text"),
    Column("用途", "text"),
    Column("关联订单号", "text"),
    Column("金额(元)", "cents"),
    Column("退款金额(元)", "cents"),
    Column("退款时间", "datetime"),
    Column("收款渠道", "text"),
    Column("状态", "text"),
    Column("创建时间", "datetime"),
)

WALLET_COLUMNS = (
    Column("记账时间", "datetime"),
    Column("账户", "text"),
    Column("账户类型", "text"),
    Column("流水类型", "text"),
    Column("金额(元)", "cents"),
    Column("变动前余额(元)", "cents"),
    Column("变动后余额(元)", "cents"),
    Column("关联支付单号", "text"),
    Column("关联订单号", "text"),
    Column("备注", "text"),
)

MONTHLY_COLUMNS = (
    Column("月份", "text"),
    Column("现金收款(元)", "cents"),
    Column("现金退款(元)", "cents"),
    Column("现金净额(元)", "cents"),
    Column("余额消费(元)", "cents"),
    Column("佣金(元)", "cents"),
    Column("新增客户", "int"),
    Column("新增销售", "int"),
)

_COLUMNS = {
    "orders": ORDER_COLUMNS,
    "payments": PAYMENT_COLUMNS,
    "wallet": WALLET_COLUMNS,
    "monthly": MONTHLY_COLUMNS,
}


class ExportError(ValueError):
    """入参不合法。带上状态码，路由层不必靠字符串猜。"""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def columns_of(kind: str) -> tuple[Column, ...]:
    if kind not in _COLUMNS:
        raise ExportError(f"不支持的导出类型：{kind}", 404)
    return _COLUMNS[kind]


def resolve_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """默认最近 30 天（含今天），与财务概览页的默认区间一致。"""
    end_day = date_to or local_date_of(utcnow())
    start_day = date_from or end_day.fromordinal(end_day.toordinal() - 29)
    if end_day < start_day:
        raise ExportError("结束日期不能早于开始日期")
    if (end_day - start_day).days + 1 > MAX_RANGE_DAYS:
        raise ExportError(f"单次导出最多 {MAX_RANGE_DAYS} 天，请拆分区间")
    return start_day, end_day


def _money(value: Any) -> Decimal | None:
    """把 float / Decimal / None 统一成两位小数的 Decimal，None 保持 None。"""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _format_cell(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == "money":
        return f"{Decimal(str(value)):.2f}"
    if kind == "cents":
        return f"{(Decimal(int(value)) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
    if kind == "int":
        return str(int(value))
    if kind == "datetime":
        return local_stamp(value) if not isinstance(value, str) else value
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _label(mapping: dict[str, str], key: str | None) -> str:
    if not key:
        return ""
    return mapping.get(key, key)


# --- 各表行生成 ------------------------------------------------------------

def _order_rows(db: Session, start_utc, end_utc) -> Iterator[list[Any]]:
    paid_sq = (
        select(PaymentOrder.order_id.label("order_id"), func.max(PaymentOrder.paid_at).label("paid_at"))
        .where(PaymentOrder.order_id.is_not(None), PaymentOrder.status.in_(("paid", "refunded")))
        .group_by(PaymentOrder.order_id)
        .subquery()
    )
    stmt = (
        select(Order, Customer.name, User.display_name, paid_sq.c.paid_at)
        .outerjoin(Customer, Customer.id == Order.customer_id)
        .outerjoin(User, User.id == Order.assigned_sales_id)
        .outerjoin(paid_sq, paid_sq.c.order_id == Order.id)
        .where(Order.created_at >= start_utc, Order.created_at < end_utc)
        .order_by(Order.created_at, Order.id)
        .execution_options(yield_per=500)
    )
    for order, customer_name, sales_name, paid_at in db.execute(stmt):
        revenue = _money(order.total_amount)
        cost = _money(order.supplier_cost)
        # 成本缺失时毛利留空：自营商品的成本不在系统里，"没算"不等于"零成本"。
        margin = None if cost is None else revenue - cost
        yield [
            order.order_no,
            order.created_at,
            paid_at,
            customer_name or "",
            sales_name or "",
            order.product_name,
            order.region_name,
            order.resource_category or "",
            order.quantity,
            order.billing_days,
            _money(order.unit_price),
            revenue,
            revenue if order.payment_status in {"paid", "partially_refunded", "refunded"} else None,
            _label(PAYMENT_STATUS, order.payment_status),
            _label(ORDER_STATUS, order.status),
            cost,
            margin,
        ]


def _payment_rows(db: Session, start_utc, end_utc) -> Iterator[list[Any]]:
    stmt = (
        select(PaymentOrder, User.display_name, Order.order_no, PaymentRefund)
        .outerjoin(User, User.id == PaymentOrder.user_id)
        .outerjoin(Order, Order.id == PaymentOrder.order_id)
        .outerjoin(PaymentRefund, PaymentRefund.payment_order_id == PaymentOrder.id)
        .where(PaymentOrder.paid_at >= start_utc, PaymentOrder.paid_at < end_utc,
               PaymentOrder.status.in_(("paid", "refunded")))
        .order_by(PaymentOrder.paid_at, PaymentOrder.id)
        .execution_options(yield_per=500)
    )
    for payment, account, order_no, refund in db.execute(stmt):
        yield [
            payment.paid_at,
            payment.out_trade_no,
            payment.platform_trade_no or "",
            account or "",
            _label(PAYMENT_PURPOSE, payment.purpose),
            order_no or "",
            payment.amount_cents,
            refund.amount_cents if refund and refund.status == "completed" else None,
            refund.created_at if refund and refund.status == "completed" else None,
            _label(PAYMENT_CHANNEL, payment.channel),
            _label(PAYMENT_STATE, payment.status),
            payment.created_at,
        ]


def _wallet_rows(db: Session, start_utc, end_utc) -> Iterator[list[Any]]:
    stmt = (
        select(WalletTransaction, User.display_name, User.role, PaymentOrder.out_trade_no, Order.order_no)
        .join(WalletAccount, WalletAccount.id == WalletTransaction.wallet_account_id)
        .outerjoin(User, User.id == WalletAccount.user_id)
        .outerjoin(PaymentOrder, PaymentOrder.id == WalletTransaction.payment_order_id)
        .outerjoin(Order, Order.id == WalletTransaction.order_id)
        .where(WalletTransaction.created_at >= start_utc, WalletTransaction.created_at < end_utc)
        .order_by(WalletTransaction.created_at, WalletTransaction.id)
        .execution_options(yield_per=500)
    )
    for entry, account_name, role, out_trade_no, order_no in db.execute(stmt):
        yield [
            entry.created_at,
            account_name or "",
            _label(ROLE_LABEL, role),
            _label(WALLET_TYPE, entry.transaction_type),
            entry.amount_cents,
            entry.balance_before_cents,
            entry.balance_after_cents,
            out_trade_no or "",
            order_no or "",
            entry.remark or "",
        ]


def _monthly_rows(db: Session, start_day: date, end_day: date, start_utc, end_utc) -> Iterator[list[Any]]:
    # 现金口径：只有 online / offline 是真钱进出。余额支付是客户存量资金在内部消耗，
    # 不能算成本月收入，否则「充值 100 再用余额付 100」会被记成 200。
    buckets = {
        key: {"cash_in": 0, "cash_refund": 0, "spent": 0, "commission": 0, "customers": 0, "sales": 0}
        for key in month_keys(start_day, end_day)
    }

    def bucket(at) -> dict[str, int] | None:
        return buckets.get(local_month_of(at))

    payments = db.execute(
        select(PaymentOrder).where(PaymentOrder.paid_at >= start_utc, PaymentOrder.paid_at < end_utc,
                                   PaymentOrder.status.in_(("paid", "refunded")))
    ).scalars().all()
    for item in payments:
        slot = bucket(item.paid_at)
        if slot is None:
            continue
        if item.channel in {"online", "offline"}:
            slot["cash_in"] += item.amount_cents
        elif item.channel == "balance":
            slot["spent"] += item.amount_cents

    # 退款归属按**退款发生时间**，不是原支付时间：跨月退款要记在退的那个月，
    # 否则上月收入会被追溯改写，对不上已经出过的报表。
    refunds = db.execute(
        select(PaymentRefund).where(PaymentRefund.created_at >= start_utc, PaymentRefund.created_at < end_utc,
                                    PaymentRefund.status == "completed")
    ).scalars().all()
    channel_of = {item.id: item.channel for item in payments}
    pending_ids = [item.payment_order_id for item in refunds if item.payment_order_id not in channel_of]
    if pending_ids:
        for payment_id, channel in db.execute(
            select(PaymentOrder.id, PaymentOrder.channel).where(PaymentOrder.id.in_(pending_ids))
        ):
            channel_of[payment_id] = channel
    for item in refunds:
        slot = bucket(item.created_at)
        if slot is None:
            continue
        if channel_of.get(item.payment_order_id) in {"online", "offline"}:
            slot["cash_refund"] += item.amount_cents

    for entry in db.execute(
        select(SalesCommissionEntry).where(SalesCommissionEntry.created_at >= start_utc,
                                           SalesCommissionEntry.created_at < end_utc)
    ).scalars():
        slot = bucket(entry.created_at)
        if slot is not None:
            slot["commission"] += entry.amount_cents

    for created_at, in db.execute(
        select(Customer.created_at).where(Customer.user_id.is_not(None),
                                          Customer.created_at >= start_utc, Customer.created_at < end_utc)
    ):
        slot = bucket(created_at)
        if slot is not None:
            slot["customers"] += 1

    for created_at, in db.execute(
        select(User.created_at).where(User.role == "sales",
                                      User.created_at >= start_utc, User.created_at < end_utc)
    ):
        slot = bucket(created_at)
        if slot is not None:
            slot["sales"] += 1

    for key in sorted(buckets):
        slot = buckets[key]
        yield [
            key,
            slot["cash_in"],
            slot["cash_refund"],
            slot["cash_in"] - slot["cash_refund"],
            slot["spent"],
            slot["commission"],
            slot["customers"],
            slot["sales"],
        ]


def iter_rows(db: Session, kind: str, start_day: date, end_day: date) -> Iterator[list[Any]]:
    """按导出类型产出原始行（未格式化）。表头不在这里，由 `columns_of` 决定。"""
    columns_of(kind)  # 校验 kind
    start_utc, end_utc = local_day_range_utc(start_day, end_day)
    if kind == "orders":
        yield from _order_rows(db, start_utc, end_utc)
    elif kind == "payments":
        yield from _payment_rows(db, start_utc, end_utc)
    elif kind == "wallet":
        yield from _wallet_rows(db, start_utc, end_utc)
    else:
        yield from _monthly_rows(db, start_day, end_day, start_utc, end_utc)


def iter_csv(kind: str, rows: Iterator[list[Any]], chunk_rows: int = 400) -> Iterator[tuple[str, int]]:
    """把行编码成 CSV 文本块，每块附带「已写出的数据行数」。

    调用方要拿最终行数写审计，而流式响应发出去就收不回来了，所以行数只能随块带出去，
    不能在结束后再统计一遍。表头永远先写进缓冲，因此即使一条数据都没有也会产出至少一块，
    否则前端会拿到一个 0 字节的文件。
    """
    columns = columns_of(kind)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([column.title for column in columns])
    total = 0
    pending = 0
    for row in rows:
        total += 1
        if len(row) != len(columns):
            # 行列数不一致说明生成逻辑和表头定义脱节了。宁可整次导出失败，
            # 也不要把错位的金额交给财务——错位的报表比没有报表危险得多。
            raise ExportError(f"{KIND_TITLES[kind]}第 {total} 行列数不一致："
                              f"期望 {len(columns)}，实际 {len(row)}", 500)
        writer.writerow([_format_cell(value, column.kind) for value, column in zip(row, columns)])
        pending += 1
        if pending >= chunk_rows:
            yield buffer.getvalue(), total
            buffer.seek(0)
            buffer.truncate(0)
            pending = 0
    if buffer.tell():
        yield buffer.getvalue(), total
