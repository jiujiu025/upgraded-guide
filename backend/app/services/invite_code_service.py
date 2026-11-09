"""销售邀请码：生成、校验、注册绑定。

设计约束（docs/销售邀请码客户归属设计.md）：
- code 只能系统生成：Crockford Base32（去掉 0/O/1/I/L），8 位 ≈ 2^40，配合注册
  IP 限流不可枚举；不可自定义字符串，防钓鱼（"VIP888" 会被客户当折扣码）。
- 一个销售最多 10 个 active 码（分渠道投放），超限 422。
- 失效码不拒绝注册：归属是商家的内部状态，不是客户的错；注册照常成功归公海，
  通知管理员 + 审计记拒绝原因。
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SalesInviteCode, User
from app.models.base import utcnow

# 去掉易混淆字符的字母表：无 0/O/1/I/L。
CROCKFORD_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8
MAX_ACTIVE_CODES_PER_SALES = 10


class InviteCodeError(Exception):
    """邀请码本身的问题（无效/过期/停用/超限/持有人不可用）。"""


def normalize_code(raw: str | None) -> str | None:
    """注册入口的码统一大写、去首尾空白；空串视为未填。"""
    if raw is None:
        return None
    normalized = raw.strip().upper()
    return normalized or None


def _generate_code() -> str:
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(CODE_LENGTH))


def create_invite_code(db: Session, sales_user: User, *, note: str | None = None,
                       max_uses: int | None = None, expires_at=None) -> SalesInviteCode:
    if sales_user.role != "sales" or not sales_user.is_active:
        raise HTTPException(status_code=403, detail="只有启用中的销售账号可以生成邀请码")
    if max_uses is not None and max_uses < 1:
        raise HTTPException(status_code=422, detail="使用次数上限必须大于 0")
    active_count = len(db.scalars(select(SalesInviteCode.id).where(
        SalesInviteCode.sales_user_id == sales_user.id,
        SalesInviteCode.status == "active",
    )).all())
    if active_count >= MAX_ACTIVE_CODES_PER_SALES:
        raise HTTPException(status_code=422, detail=f"有效邀请码最多 {MAX_ACTIVE_CODES_PER_SALES} 个，请先停用再新建")
    for _ in range(10):
        candidate = _generate_code()
        if not db.scalar(select(SalesInviteCode.id).where(SalesInviteCode.code == candidate)):
            code = SalesInviteCode(code=candidate, sales_user_id=sales_user.id,
                                   note=note, max_uses=max_uses, expires_at=expires_at)
            db.add(code)
            db.flush()
            return code
    raise HTTPException(status_code=503, detail="邀请码生成冲突，请重试")


def lookup_active_code(db: Session, raw_code: str) -> tuple[SalesInviteCode, User] | None:
    """按注册入口的语义查"当前有效"的码；返回 (码, 持有销售)。

    返回 None 时 caller 应按"失效码不拒绝注册"处理。这里的判断集中在一处：
    active 状态、未过期、未超使用上限、持有人仍是启用中的销售。
    """
    code = db.scalar(select(SalesInviteCode).where(SalesInviteCode.code == normalize_code(raw_code)))
    if code is None or code.status != "active":
        return None
    now = utcnow()
    if code.expires_at is not None and code.expires_at <= now:
        return None
    if code.max_uses is not None and code.used_count >= code.max_uses:
        return None
    holder = db.get(User, code.sales_user_id)
    if holder is None or holder.role != "sales" or not holder.is_active:
        return None
    return code, holder


def disable_invite_code(db: Session, code: SalesInviteCode, actor: User) -> SalesInviteCode:
    """停用（销售停自己的 / 管理员强制停用共用此入口）。"""
    if code.status == "disabled":
        return code
    code.status = "disabled"
    code.disabled_at = utcnow()
    code.disabled_by = actor.id
    db.flush()
    return code


def disable_codes_of_sales(db: Session, sales_user_id: int, actor: User) -> int:
    """销售账号被停用时级联停用其全部 active 码；返回停用数量。"""
    codes = db.scalars(select(SalesInviteCode).where(
        SalesInviteCode.sales_user_id == sales_user_id,
        SalesInviteCode.status == "active",
    )).all()
    for code in codes:
        disable_invite_code(db, code, actor)
    return len(codes)
