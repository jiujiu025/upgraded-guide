"""模拟登录接口：超级管理员以销售/客户身份进入，10 分钟后自动失效。

设计见 docs/管理员模拟登录设计.md。要点：
- 只有 super_admin 能发起，只能模拟启用中的销售/客户；
- 不能链式模拟（已经是模拟态时禁止再发起）；
- token 独立于真实会话，可立刻吊销，到期自动失效；
- 结束模拟只结束这一条记录，不影响管理员自己的后台登录。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import ImpersonationSession, User
from app.services.audit_service import write_audit
from app.services.impersonation_service import (
    TTL_MINUTES,
    ImpersonationError,
    context_of,
    end,
    mark_expired,
    end_by_token,
    snapshot,
    start,
    target_customer_missing,
)
from app.services.session_service import SessionError

router = APIRouter(prefix="/admin/impersonation", tags=["管理员模拟登录"])


class StartImpersonationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


@router.post("")
def start_impersonation(
    payload: StartImpersonationIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    if context_of(db) is not None:
        raise HTTPException(status_code=403, detail="模拟登录中不能再发起新的模拟，请先退出当前模拟")
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if target_customer_missing(db, target):
        raise HTTPException(status_code=422, detail="该客户账号没有绑定客户档案，模拟进去也是空页面")

    try:
        record, raw = start(db, user, target, ip=_client_ip(request),
                            user_agent=request.headers.get("user-agent"))
    except ImpersonationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    write_audit(db, user, "impersonation_start", "user", target.id,
                f"开始模拟 {target.display_name or target.username}（{target.role}，{TTL_MINUTES} 分钟）")
    db.commit()
    db.refresh(record)
    return {
        "token": raw,
        "expires_at": record.expires_at.isoformat(),
        "ttl_minutes": TTL_MINUTES,
        "user": {
            "id": target.id, "username": target.username,
            "display_name": target.display_name, "role": target.role,
        },
        "actor": {"id": user.id, "display_name": user.display_name or user.username},
    }


@router.get("")
def list_impersonations(
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    # 管理员打开列表时顺手清理过期记录，避免"进行中"里堆着早就失效的会话
    if mark_expired(db):
        db.commit()
    query = select(ImpersonationSession).order_by(ImpersonationSession.id.desc()).limit(limit)
    if active_only:
        query = select(ImpersonationSession).where(
            ImpersonationSession.ended_at.is_(None)
        ).order_by(ImpersonationSession.id.desc()).limit(limit)
    records = list(db.scalars(query).all())
    users = {item.id: item for item in db.scalars(
        select(User).where(User.id.in_({r.actor_id for r in records} | {r.target_user_id for r in records}))
    ).all()}
    return [
        snapshot(record, users.get(record.actor_id), users.get(record.target_user_id))
        for record in records
    ]


@router.delete("/{record_id}")
def force_end(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    record = db.get(ImpersonationSession, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="模拟记录不存在")
    if record.ended_at is not None:
        return {"ok": True, "already_ended": True}
    end(db, record, "forced")
    write_audit(db, user, "impersonation_end", "user", record.target_user_id,
                f"强制结束模拟（记录 #{record.id}）")
    db.commit()
    return {"ok": True}


auth_router = APIRouter(prefix="/auth", tags=["认证"])


@auth_router.post("/impersonation/logout", status_code=204)
def logout_impersonation(
    request: Request,
    db: Session = Depends(get_db),
):
    """模拟态自己退出。只结束这一条模拟记录，管理员的后台会话不受影响。"""
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少模拟令牌")
    try:
        end_by_token(db, token)
    except SessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    db.commit()
    return None
