"""任务注册表：调度进程按这里的间隔轮询执行。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.tasks import lines, orders, payments, suppliers


@dataclass(frozen=True)
class TaskSpec:
    name: str
    title: str
    interval_minutes: int
    handler: Callable[..., dict]


def _close_expired_orders(db: Session, now: datetime) -> dict:
    settings = get_settings()
    return orders.close_expired_orders(db, now=now, timeout_minutes=settings.order_payment_timeout_minutes)


def _line_expiry_reminders(db: Session, now: datetime) -> dict:
    settings = get_settings()
    return lines.line_expiry_reminders(db, now=now, reminder_days=settings.line_reminder_day_list)


def _payment_reconcile(db: Session, now: datetime) -> dict:
    settings = get_settings()
    return payments.reconcile_payments(db, now=now, grace_minutes=settings.payment_reconcile_grace_minutes)


def _supplier_catalog_refresh(db: Session, now: datetime) -> dict:
    return suppliers.refresh_catalog(db, now=now)


def _supplier_quotes_refresh(db: Session, now: datetime) -> dict:
    return suppliers.refresh_quotes(db, now=now)


def _supplier_inventory_refresh(db: Session, now: datetime) -> dict:
    settings = get_settings()
    return suppliers.refresh_inventory(
        db,
        now=now,
        batch_size=settings.supplier_inventory_batch_size,
        stale_minutes=settings.supplier_inventory_ttl_minutes,
    )


# 顺序即执行顺序：先跑影响钱和客户的，再跑上游刷新；库存是唯一需要逐个国家
# 串行请求的任务，放最后，避免它拖慢同一轮里的关单和到期提醒。
SPECS = (
    TaskSpec("payment_reconcile", "支付对账与挂单清理", 15, _payment_reconcile),
    TaskSpec("close_expired_orders", "关闭超时未支付订单", 30, _close_expired_orders),
    TaskSpec("line_expiry_reminders", "线路到期提醒", 360, _line_expiry_reminders),
    TaskSpec("supplier_catalog_refresh", "供应商目录刷新", 360, _supplier_catalog_refresh),
    TaskSpec("supplier_quotes_refresh", "供应商报价刷新", 360, _supplier_quotes_refresh),
    TaskSpec("supplier_inventory_refresh", "供应商库存刷新（按国家轮转）", 30, _supplier_inventory_refresh),
)

TASKS: dict[str, TaskSpec] = {spec.name: spec for spec in SPECS}
