"""线路连通性检测：TCP 探测 + 写回节点状态与故障事件。

拆成三段，是为了让批量检测能并发跑：
``probe`` 只做网络 IO（无数据库会话，可丢线程池）；
``apply_result`` 只写库（单条，不提交）；
``check_node`` 是两者的组合，保持原来"单条检测并提交"的行为不变。

不并发的话，管理员点一次"全量检测"，100 条线路、每条 3 秒超时，会卡 5 分钟。
"""
from __future__ import annotations

import socket
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerLine, HealthIncident, Node, Notification

PROBE_TIMEOUT = 3.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> tuple[bool, int | None, str | None]:
    """返回 (是否连通, 延迟毫秒, 失败原因)。不碰数据库，可在线程池里跑。"""
    if not host or not port:
        return False, None, "缺少地址或端口"
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            latency = max(1, round((time.monotonic() - started) * 1000))
            return True, latency, None
    except (OSError, TimeoutError) as exc:
        return False, None, str(exc)[:255]


def apply_result(db: Session, node: Node, ok: bool, latency: int | None, reason: str | None) -> Node:
    """把一次探测结果写回节点；掉线时开故障事件并通知受影响客户。不提交事务。"""
    if ok:
        node.health_status = "healthy"
        node.health_detail = "TCP 连接成功"
        node.health_latency_ms = latency
        incident = db.scalar(select(HealthIncident).where(
            HealthIncident.node_id == node.id, HealthIncident.status == "open"))
        if incident:
            incident.status = "recovered"
            incident.recovered_at = _utcnow()
    else:
        node.health_status = "offline"
        node.health_detail = "TCP 连接失败"
        node.health_latency_ms = None
        open_incident = db.scalar(select(HealthIncident).where(
            HealthIncident.node_id == node.id, HealthIncident.status == "open"))
        if not open_incident:
            db.add(HealthIncident(node_id=node.id, fault_type="tcp", reason=(reason or "")[:255],
                                  started_at=_utcnow()))
            customers = db.execute(
                select(Customer.user_id, Customer.sales_id)
                .join(CustomerLine, CustomerLine.customer_id == Customer.id)
                .where(CustomerLine.node_id == node.id, Customer.is_deleted.is_(False))
            ).all()
            recipients = {recipient for row in customers for recipient in row if recipient}
            for recipient in recipients:
                db.add(Notification(user_id=recipient, title="线路健康异常",
                                    content="你关联的一条线路出现健康异常，运维团队正在处理。",
                                    category="health"))
    node.health_checked_at = _utcnow()
    return node


def check_node(db: Session, node: Node) -> Node:
    ok, latency, reason = probe(node.server, node.port)
    apply_result(db, node, ok, latency, reason)
    db.commit()
    db.refresh(node)
    return node
