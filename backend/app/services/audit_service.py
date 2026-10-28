from sqlalchemy.orm import Session

from app.models import AuditLog, User


def write_audit(db: Session, actor: User, action: str, resource_type: str, resource_id: int | str | None = None, detail: str | None = None) -> None:
    db.add(AuditLog(actor_id=actor.id, action=action, resource_type=resource_type, resource_id=str(resource_id) if resource_id else None, detail=detail))
