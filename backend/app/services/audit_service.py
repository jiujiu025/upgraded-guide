from sqlalchemy.orm import Session

from app.models import AuditLog, User


def write_audit(
    db: Session,
    actor: User | None,
    action: str,
    resource_type: str,
    resource_id: int | str | None = None,
    detail: str | None = None,
) -> None:
    """写一条审计。actor 传 None 表示系统动作（回调、定时任务、自动采购）。

    系统动作必须留痕：出了问题要能回答"这条线路是谁开的"，哪怕答案是"系统自动开的"。
    """
    # 模拟登录：操作算在发起的管理员头上，并在 detail 里标明模拟的是谁。
    # 不能记成"客户自己操作的"——出事要能追到人。
    context = db.info.get("impersonation")
    if context is not None:
        actor_id = context.actor_id
        detail = f"[模拟 {context.target_name}] {detail or ''}".strip()
    else:
        actor_id = actor.id if actor is not None else None
    db.add(AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        detail=detail,
    ))
