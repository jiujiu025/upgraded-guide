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
    db.add(AuditLog(
        actor_id=actor.id if actor is not None else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        detail=detail,
    ))
