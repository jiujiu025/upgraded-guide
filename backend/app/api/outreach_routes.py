"""销售端到期触达接口。

只有三个动作：看待触达清单、记一次触达、看历史。没有"群发"接口——
真正的发送发生在销售自己的微信里，系统不该假装能替它发，也不该在没有通道的情况下
写一个点了没反应的发按钮。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import User
from app.services.renewal_outreach_service import (
    CHANNELS,
    OUTCOMES,
    OutreachError,
    outreach_queue,
    record_touch,
    summary_of,
    touch_history,
)

router = APIRouter(prefix="/sales/renewal-outreach", tags=["销售到期触达"])


class TouchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(pattern="^(wechat|phone|email|other)$")
    outcome: str = Field(pattern="^(sent|promised|declined|unreachable)$")
    # 实际发出去的话。不传就用模板生成的，传了就以传入的为准。
    message: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)


def _fail(exc: OutreachError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("")
def list_outreach(
    days: int = Query(default=30, ge=1, le=180),
    overdue_grace_days: int = Query(default=15, ge=0, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales", "admin", "super_admin")),
):
    items = outreach_queue(db, user=user, days=days, overdue_grace_days=overdue_grace_days)
    return {
        "items": items,
        "summary": summary_of(items),
        "channels": [{"value": key, "label": label} for key, label in CHANNELS.items()],
        "outcomes": [{"value": key, "label": label} for key, label in OUTCOMES.items()],
        "window_days": days,
        "overdue_grace_days": overdue_grace_days,
    }


# 必须放在 /{line_id} 之前：否则 "history" 会被当成路径参数去转 int。
@router.get("/history")
def list_history(
    line_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales", "admin", "super_admin")),
):
    return {"items": touch_history(db, user=user, line_id=line_id, limit=limit)}


@router.post("/{line_id}", status_code=201)
def create_touch(
    line_id: int,
    payload: TouchIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales", "admin", "super_admin")),
):
    try:
        touch = record_touch(
            db,
            user=user,
            line_id=line_id,
            channel=payload.channel,
            outcome=payload.outcome,
            message=payload.message,
            note=payload.note,
        )
    except OutreachError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {
        "id": touch.id,
        "line_id": touch.line_id,
        "channel": touch.channel,
        "channel_label": CHANNELS.get(touch.channel, touch.channel),
        "outcome": touch.outcome,
        "outcome_label": OUTCOMES.get(touch.outcome, touch.outcome),
        "message": touch.message,
        "note": touch.note,
        "days_left": touch.days_left,
        "created_at": touch.created_at,
    }
