"""模拟登录：超级管理员以销售/客户身份进入系统。

安全边界（与 docs/管理员模拟登录设计.md 对应）：

- token 独立于真实会话，10 分钟硬过期，可在后台立刻吊销；
- 只有 super_admin 能发起，只能模拟 **启用中的销售/客户**，且不能模拟管理员；
- 不能链式模拟：已经是模拟态时不能再发起；
- 审计记在管理员头上，并标注"模拟某客户"，不允许变成"客户自己操作的"；
- 身份类操作（改密码、改密保）在模拟态下拒绝：否则会把客户锁在门外，
  而这件事本来就有后台「重置密码」可做，不需要借客户的壳。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Customer, ImpersonationSession, User
from app.models.base import utcnow
from app.services.session_service import SessionError

TOKEN_PREFIX = "imp_"
TOKEN_BYTES = 32
TTL_MINUTES = 10
MAX_ACTIVE_PER_ACTOR = 3
ALLOWED_TARGET_ROLES = ("sales", "customer")
# 模拟态下不允许改的东西：改完客户就登不进来了，属于"用管理员权限伤害客户"
BLOCKED_PATHS = (
    "/api/auth/change-password",
    "/api/auth/security-questions",
    "/api/auth/password-recovery",
)


class ImpersonationError(Exception):
    def __init__(self, message: str, *, code: str = "IMPERSONATION_ERROR", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ImpersonationContext:
    record_id: int
    actor_id: int
    actor_name: str
    target_id: int
    target_name: str


def _hash(raw: str) -> str:
    settings = get_settings()
    secret = settings.session_token_hash_key.get_secret_value() if settings.session_token_hash_key else settings.jwt_secret_key
    return hmac.new(("impersonation:" + secret).encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def _display(user: User) -> str:
    return user.display_name or user.username


def start(db: Session, actor: User, target: User, *, ip: str | None = None,
          user_agent: str | None = None) -> tuple[ImpersonationSession, str]:
    if actor.role != "super_admin":
        raise ImpersonationError("只有超级管理员可以模拟登录", code="NOT_SUPER_ADMIN", status=403)
    if target.id == actor.id:
        raise ImpersonationError("不能模拟自己")
    if target.role not in ALLOWED_TARGET_ROLES:
        raise ImpersonationError("只能模拟销售或客户账号", code="TARGET_ROLE_NOT_ALLOWED")
    if not target.is_active:
        raise ImpersonationError("该账号已停用，不能模拟", code="TARGET_DISABLED")

    active = db.scalar(
        select(func.count()).select_from(ImpersonationSession).where(
            ImpersonationSession.actor_id == actor.id,
            ImpersonationSession.ended_at.is_(None),
            ImpersonationSession.expires_at > utcnow(),
        )
    ) or 0
    if active >= MAX_ACTIVE_PER_ACTOR:
        raise ImpersonationError(f"同时最多模拟 {MAX_ACTIVE_PER_ACTOR} 个账号，先结束一个再试", code="TOO_MANY_ACTIVE")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    now = utcnow()
    record = ImpersonationSession(
        actor_id=actor.id, target_user_id=target.id, token_hash=_hash(raw),
        started_at=now, expires_at=now + timedelta(minutes=TTL_MINUTES),
        actor_ip=(ip or "")[:64] or None, user_agent=(user_agent or "")[:255] or None,
    )
    db.add(record)
    db.flush()
    return record, raw


def resolve(db: Session, raw: str) -> tuple[ImpersonationSession, User]:
    """校验模拟 token 并返回目标用户。任何一条不满足都按"登录已失效"处理。"""
    if not raw.startswith(TOKEN_PREFIX):
        raise SessionError("登录已失效")
    record = db.scalar(select(ImpersonationSession).where(ImpersonationSession.token_hash == _hash(raw)))
    if record is None:
        raise SessionError("模拟登录已失效")
    if record.ended_at is not None:
        raise SessionError("模拟登录已结束")
    if record.expires_at <= utcnow():
        # 这里不写库：current_user 会立刻抛 401，请求事务回滚，写了也留不下。
        # 过期记录由列表接口（管理员查看时）统一补标 expired。
        raise SessionError("模拟登录已到期")

    actor = db.get(User, record.actor_id)
    target = db.get(User, record.target_user_id)
    # 发起人被停用/降权后，他开的模拟必须立即失效，不能留着当后门
    if actor is None or not actor.is_active or actor.role != "super_admin":
        record.ended_at = utcnow()
        record.end_reason = "actor_disabled"
        db.flush()
        raise SessionError("模拟登录已失效")
    if target is None or not target.is_active:
        record.ended_at = utcnow()
        record.end_reason = "target_disabled"
        db.flush()
        raise SessionError("被模拟账号不可用")

    now = utcnow()
    if record.last_seen_at is None or (now - record.last_seen_at) > timedelta(seconds=60):
        record.last_seen_at = now
        db.flush()

    db.info["impersonation"] = ImpersonationContext(
        record_id=record.id, actor_id=actor.id, actor_name=_display(actor),
        target_id=target.id, target_name=_display(target),
    )
    return record, target


def mark_expired(db: Session) -> int:
    """把已过期但仍标"进行中"的记录补成 expired。由列表接口调用并提交。"""
    rows = db.scalars(select(ImpersonationSession).where(
        ImpersonationSession.ended_at.is_(None),
        ImpersonationSession.expires_at <= utcnow(),
    )).all()
    for record in rows:
        record.ended_at = utcnow()
        record.end_reason = "expired"
    if rows:
        db.flush()
    return len(rows)


def context_of(db: Session) -> ImpersonationContext | None:
    return db.info.get("impersonation")


def end(db: Session, record: ImpersonationSession, reason: str = "manual") -> None:
    if record.ended_at is None:
        record.ended_at = utcnow()
        record.end_reason = reason
        db.flush()


def end_by_token(db: Session, raw: str) -> None:
    record = db.scalar(select(ImpersonationSession).where(ImpersonationSession.token_hash == _hash(raw)))
    if record is not None:
        end(db, record, "manual")


def active_count_before_start(db: Session, actor_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(ImpersonationSession).where(
            ImpersonationSession.actor_id == actor_id,
            ImpersonationSession.ended_at.is_(None),
            ImpersonationSession.expires_at > utcnow(),
        )
    ) or 0


def target_customer_missing(db: Session, target: User) -> bool:
    """客户账号没有绑定客户档案时，模拟进去只会是空页面，提前挡住。"""
    if target.role != "customer":
        return False
    customer = db.scalar(select(Customer).where(
        Customer.user_id == target.id, Customer.is_deleted.is_(False)))
    return customer is None


def snapshot(record: ImpersonationSession, actor: User | None, target: User | None) -> dict:
    now = utcnow()
    remaining = max(0, int((record.expires_at - now).total_seconds())) if record.ended_at is None else 0
    return {
        "id": record.id,
        "actor_id": record.actor_id,
        "actor_name": _display(actor) if actor else None,
        "target_user_id": record.target_user_id,
        "target_name": _display(target) if target else None,
        "target_role": target.role if target else None,
        "started_at": record.started_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "end_reason": record.end_reason,
        "remaining_seconds": remaining,
        "active": record.ended_at is None and remaining > 0,
        "actor_ip": record.actor_ip,
        "user_agent": record.user_agent,
    }
