"""可在后台改、立即生效的开关。

读取顺序：runtime_settings 表 → 环境变量默认值。表里没有记录时才回退，
这样部署时可以用环境变量定一个安全默认值，运营再在后台覆盖。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import RuntimeSetting, User
from app.services.audit_service import write_audit

AUTO_PROCUREMENT_KEY = "auto_procurement"

_TRUE = {"1", "true", "yes", "on"}


def _default(key: str) -> bool:
    if key == AUTO_PROCUREMENT_KEY:
        return bool(get_settings().supplier_auto_procurement_enabled)
    return False


def get_flag(db: Session, key: str) -> tuple[bool, str]:
    """返回 (当前值, 来源)。来源是 runtime 或 env，后台据此显示"后台设置/默认"。"""
    row = db.scalar(select(RuntimeSetting).where(RuntimeSetting.key == key))
    if row is None:
        return _default(key), "env"
    return row.value.strip().lower() in _TRUE, "runtime"


def set_flag(db: Session, key: str, value: bool, actor: User | None = None) -> RuntimeSetting:
    row = db.scalar(select(RuntimeSetting).where(RuntimeSetting.key == key))
    text = "true" if value else "false"
    if row is None:
        row = RuntimeSetting(key=key, value=text, updated_by=actor.id if actor else None)
        db.add(row)
    else:
        row.value = text
        row.updated_by = actor.id if actor else None
    if actor is not None:
        write_audit(db, actor, "runtime_setting", "runtime_setting", None, f"{key}={text}")
    db.flush()
    return row


def auto_procurement_enabled(db: Session) -> bool:
    return get_flag(db, AUTO_PROCUREMENT_KEY)[0]
