"""任务注册表：调度进程按这里的间隔轮询执行。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.tasks import lines, orders, payments


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


SPECS = (
    TaskSpec("payment_reconcile", "支付对账与挂单清理", 15, _payment_reconcile),
    TaskSpec("close_expired_orders", "关闭超时未支付订单", 30, _close_expired_orders),
    TaskSpec("line_expiry_reminders", "线路到期提醒", 360, _line_expiry_reminders),
)

TASKS: dict[str, TaskSpec] = {spec.name: spec for spec in SPECS}
