import socket
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerLine, HealthIncident, Node, Notification


def check_node(db: Session, node: Node) -> Node:
    started = datetime.now(timezone.utc).timestamp()
    try:
        with socket.create_connection((node.server, node.port), timeout=3):
            node.health_status = "healthy"
            node.health_detail = "TCP 连接成功"
            node.health_latency_ms = max(1, round((datetime.now(timezone.utc).timestamp() - started) * 1000))
            open_incident = db.scalar(select(HealthIncident).where(HealthIncident.node_id == node.id, HealthIncident.status == "open"))
            if open_incident:
                open_incident.status = "recovered"
                open_incident.recovered_at = datetime.now(timezone.utc).replace(tzinfo=None)
    except (OSError, TimeoutError) as exc:
        node.health_status = "offline"
        node.health_detail = "TCP 连接失败"
        node.health_latency_ms = None
        if not db.scalar(select(HealthIncident).where(HealthIncident.node_id == node.id, HealthIncident.status == "open")):
            db.add(HealthIncident(node_id=node.id, fault_type="tcp", reason=str(exc)[:255], started_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            customers = db.execute(select(Customer.user_id, Customer.sales_id).join(CustomerLine, CustomerLine.customer_id == Customer.id).where(CustomerLine.node_id == node.id, Customer.is_deleted.is_(False))).all()
            recipients = {recipient for row in customers for recipient in row if recipient}
            for recipient in recipients:
                db.add(Notification(user_id=recipient, title="线路健康异常", content="你关联的一条线路出现健康异常，运维团队正在处理。", category="health"))
    node.health_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(node)
    return node
