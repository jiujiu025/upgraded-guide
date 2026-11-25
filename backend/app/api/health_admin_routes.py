"""管理员线路健康：所有客户线路 + 面板上的所有入站，可筛选、可搜索、可批量检测。

为什么单独一套接口，而不是扩 `/health/lines`：
- 那套接口返回的是 ``Node``（同一个出口卖多个客户时会被复用），**看不出这条线是谁的**；
  管理员要的是"哪个销售、哪个客户"这一层，必须从 ``CustomerLine`` 出发再 join 回去。
- 面板入站还有一半不在系统里（人工在 3x-ui 上直接建的），只能实时去问面板。

两个视图共用同一套筛选参数，前端可以做成一个页面两个 tab。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, like_pattern, page_payload
from app.data.site_catalog import CATEGORY_TO_LINE, LINE_LABELS
from app.db import get_db
from app.models import (
    Customer, CustomerLine, Node, Order, Product, Region, TransitProvision, TransitVps, User,
)
from app.services.health_service import apply_result, probe
from app.services.transit_service import panel_credentials
from app.services.xui_client import XuiClient, loads_field

router = APIRouter(prefix="/admin/health", tags=["管理员线路健康"])

MAX_BATCH = 200
PROBE_WORKERS = 8


def _line_query():
    """一条客户线路一行。入站/出站信息来自中转记录，没有中转的线路这两列为空。"""
    return (
        select(
            CustomerLine.id.label("line_id"),
            CustomerLine.static_ip,
            CustomerLine.expires_at,
            CustomerLine.status.label("line_status"),
            CustomerLine.started_at,
            CustomerLine.order_id,
            Customer.id.label("customer_id"),
            Customer.name.label("customer_name"),
            Customer.sales_id,
            User.display_name.label("sales_name"),
            User.username.label("sales_username"),
            Product.name.label("product_name"),
            Product.resource_category,
            Region.country_code,
            Order.order_no,
            Order.region_name,
            Order.requested_city_name,
            Node.id.label("node_id"),
            Node.name.label("node_name"),
            Node.server,
            Node.port,
            Node.protocol,
            Node.health_status,
            Node.health_latency_ms,
            Node.health_checked_at,
            TransitProvision.inbound_port,
            TransitProvision.outbound_tag,
            TransitProvision.exit_ip,
            TransitProvision.remark.label("inbound_remark"),
            TransitProvision.status.label("transit_status"),
            TransitVps.name.label("vps_name"),
            TransitVps.group_code.label("vps_group"),
        )
        .join(Customer, Customer.id == CustomerLine.customer_id)
        .outerjoin(User, User.id == Customer.sales_id)
        .outerjoin(Product, Product.id == CustomerLine.product_id)
        .outerjoin(Region, Region.id == Product.region_id)
        .outerjoin(Order, Order.id == CustomerLine.order_id)
        .outerjoin(Node, Node.id == CustomerLine.node_id)
        .outerjoin(TransitProvision, TransitProvision.line_id == CustomerLine.id)
        .outerjoin(TransitVps, TransitVps.id == TransitProvision.transit_vps_id)
        .where(Customer.is_deleted.is_(False))
    )


def _apply_filters(
    query,
    *,
    sales_id: int | None,
    customer_id: int | None,
    line_code: str | None,
    country: str | None,
    health: str | None,
    keyword: str | None,
):
    if sales_id is not None:
        query = query.where(Customer.sales_id == sales_id)
    if customer_id is not None:
        query = query.where(Customer.id == customer_id)
    if line_code:
        categories = [category for category, line in CATEGORY_TO_LINE.items() if line == line_code]
        query = query.where(Product.resource_category.in_(categories))
    if country:
        query = query.where(func.upper(Region.country_code) == country.strip().upper())
    if health == "unknown":
        query = query.where(or_(Node.health_status.is_(None), Node.health_status == "unknown"))
    elif health:
        query = query.where(Node.health_status == health)
    if keyword and keyword.strip():
        pattern = like_pattern(keyword.strip())
        query = query.where(or_(
            Customer.name.ilike(pattern, escape="\\"),
            CustomerLine.static_ip.ilike(pattern, escape="\\"),
            Node.server.ilike(pattern, escape="\\"),
            Order.order_no.ilike(pattern, escape="\\"),
            Product.name.ilike(pattern, escape="\\"),
            TransitProvision.outbound_tag.ilike(pattern, escape="\\"),
            TransitProvision.remark.ilike(pattern, escape="\\"),
            TransitVps.name.ilike(pattern, escape="\\"),
        ))
    return query


def _row_dict(row) -> dict:
    data = dict(row._mapping)
    line_code = CATEGORY_TO_LINE.get(data.get("resource_category") or "", "")
    data["line_code"] = line_code
    data["line_label"] = LINE_LABELS.get(line_code, data.get("product_name") or "")
    # 客户连的入口和真实出口分开说清楚：社媒/直播连的是中转 vless，住宅连的是 socks
    data["entry"] = f"{data.get('server') or '—'}:{data.get('port') or '—'}"
    data["exit_ip"] = data.get("exit_ip") or data.get("static_ip")
    return data


def _options(db: Session) -> dict:
    sales = db.execute(
        select(User.id, User.display_name, User.username)
        .where(User.role == "sales", User.is_active.is_(True))
        .order_by(User.display_name, User.username)
    ).all()
    countries = db.execute(
        select(Region.country_code).where(Region.country_code.is_not(None)).distinct()
    ).scalars().all()
    return {
        "sales": [{"id": row[0], "name": row[1] or row[2]} for row in sales],
        "lines": [{"code": code, "label": label} for code, label in LINE_LABELS.items()],
        "countries": sorted({code.upper() for code in countries if code}),
    }


@router.get("/overview")
def health_overview(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    base = _line_query().subquery()
    total = db.scalar(select(func.count()).select_from(base)) or 0
    by_health = dict(db.execute(
        select(func.coalesce(base.c.health_status, "unknown"), func.count()).group_by(base.c.health_status)
    ).all())
    latencies = db.execute(
        select(base.c.health_latency_ms).where(base.c.health_latency_ms.is_not(None))
    ).scalars().all()
    return {
        "lines": {
            "total": total,
            "healthy": by_health.get("healthy", 0),
            "offline": by_health.get("offline", 0),
            "unknown": by_health.get("unknown", 0) + by_health.get("None", 0),
        },
        "average_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "customers": db.scalar(select(func.count()).select_from(Customer).where(Customer.is_deleted.is_(False))) or 0,
        "sales": db.scalar(select(func.count()).select_from(User).where(User.role == "sales", User.is_active.is_(True))) or 0,
        "panels": {
            "total": db.scalar(select(func.count()).select_from(TransitVps)) or 0,
            "enabled": db.scalar(select(func.count()).select_from(TransitVps).where(TransitVps.is_enabled.is_(True))) or 0,
        },
        "inbounds": {
            "total": db.scalar(select(func.count()).select_from(TransitProvision)) or 0,
            "configured": db.scalar(
                select(func.count()).select_from(TransitProvision).where(TransitProvision.status == "configured")
            ) or 0,
        },
    }


@router.get("/customer-lines")
def customer_lines(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    sales_id: int | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    line_code: str | None = Query(default=None, max_length=30),
    country: str | None = Query(default=None, max_length=10),
    health: str | None = Query(default=None, max_length=20),
    keyword: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    query = _apply_filters(
        _line_query(), sales_id=sales_id, customer_id=customer_id, line_code=line_code,
        country=country, health=health, keyword=keyword,
    )
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.execute(
        query.order_by(CustomerLine.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    # 状态计数不随 health 筛选收缩，否则选中"离线"后下拉只剩一个选项
    count_query = _apply_filters(
        _line_query(), sales_id=sales_id, customer_id=customer_id, line_code=line_code,
        country=country, health=None, keyword=keyword,
    ).subquery()
    counts = dict(db.execute(
        select(func.coalesce(count_query.c.health_status, "unknown"), func.count())
        .group_by(count_query.c.health_status)
    ).all())
    return page_payload(
        [_row_dict(row) for row in rows], total, page, page_size,
        counts={
            "healthy": counts.get("healthy", 0),
            "offline": counts.get("offline", 0),
            "unknown": counts.get("unknown", 0) + counts.get("None", 0),
        },
        options=_options(db),
    )


class LineCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 传空 + all=true 表示"当前全部"；前端筛选后可以只传当页 id，避免误测全站
    line_ids: list[int] = Field(default_factory=list, max_length=MAX_BATCH)
    all: bool = False


@router.post("/customer-lines/check")
def check_customer_lines(payload: LineCheckIn, db: Session = Depends(get_db),
                         user: User = Depends(require_roles("super_admin", "admin"))):
    query = select(CustomerLine.node_id).distinct().where(CustomerLine.node_id.is_not(None))
    if payload.line_ids:
        query = query.where(CustomerLine.id.in_(payload.line_ids[:MAX_BATCH]))
    elif not payload.all:
        return {"checked": 0, "healthy": 0, "offline": 0, "nodes": []}
    nodes = list(db.scalars(select(Node).where(Node.id.in_(query))).all())
    return _probe_nodes(db, nodes)


def _probe_nodes(db: Session, nodes: list[Node]) -> dict:
    """并发探测、串行写库。网络 IO 在池里跑，SQLAlchemy 会话始终只在当前线程用。"""
    if not nodes:
        return {"checked": 0, "healthy": 0, "offline": 0, "nodes": []}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        results = list(pool.map(lambda node: probe(node.server, node.port), nodes))
    healthy = 0
    for node, (ok, latency, reason) in zip(nodes, results):
        apply_result(db, node, ok, latency, reason)
        healthy += 1 if ok else 0
    db.commit()
    return {
        "checked": len(nodes),
        "healthy": healthy,
        "offline": len(nodes) - healthy,
        "nodes": [
            {"node_id": node.id, "server": node.server, "port": node.port,
             "status": node.health_status, "latency_ms": node.health_latency_ms}
            for node in nodes
        ],
    }


# ---------------------------------------------------------------- 面板入站
def _panel_snapshot(vps: TransitVps, db: Session) -> dict[str, Any]:
    """拉一台面板的入站与 Xray 模板，解出"入站 → 出站"的对应关系。"""
    password, token = panel_credentials(db, vps)
    with XuiClient(vps.base_url, username=vps.username, password=password, api_token=token) as client:
        inbounds = client.list_inbounds()
        template = client.get_xray_template()

    outbounds = {item.get("tag"): item for item in (template.get("outbounds") or []) if item.get("tag")}
    tag_to_outbound: dict[str, str] = {}
    for rule in (template.get("routing") or {}).get("rules") or []:
        outbound_tag = rule.get("outboundTag")
        if not outbound_tag:
            continue
        for inbound_tag in rule.get("inboundTag") or []:
            tag_to_outbound.setdefault(inbound_tag, outbound_tag)

    items = []
    for inbound in inbounds:
        port = int(inbound.get("port") or 0)
        inbound_tag = inbound.get("tag") or f"inbound-{port}"
        outbound_tag = tag_to_outbound.get(inbound_tag)
        outbound = outbounds.get(outbound_tag) or {}
        servers = (outbound.get("settings") or {}).get("servers") or []
        exit_server = servers[0] if servers else {}
        settings = loads_field(inbound.get("settings"))
        clients = settings.get("clients") or []
        reality = (loads_field(inbound.get("streamSettings")).get("realitySettings") or {})
        items.append({
            "vps_id": vps.id,
            "vps_name": vps.name,
            "group_code": vps.group_code,
            "connect_host": vps.connect_host,
            "inbound_id": inbound.get("id"),
            "port": port,
            "remark": inbound.get("remark") or "",
            "protocol": inbound.get("protocol"),
            "enable": bool(inbound.get("enable")),
            "client_count": len(clients),
            "client_email": (clients[0].get("email") if clients else None),
            "inbound_tag": inbound_tag,
            "sni": (reality.get("serverNames") or [None])[0],
            "outbound_tag": outbound_tag,
            "outbound_protocol": outbound.get("protocol"),
            "exit_host": exit_server.get("address"),
            "exit_port": exit_server.get("port"),
        })
    return {"vps": vps, "items": items}


@router.get("/transit-inbounds")
def transit_inbounds(
    vps_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(TransitVps).where(TransitVps.is_enabled.is_(True))
    if vps_id:
        query = select(TransitVps).where(TransitVps.id == vps_id)
    panels = list(db.scalars(query.order_by(TransitVps.id)).all())
    if not panels:
        return {"items": [], "panels": [], "errors": []}

    errors: list[dict] = []
    items: list[dict] = []

    def load(panel: TransitVps):
        try:
            return _panel_snapshot(panel, db), None
        except Exception as exc:  # noqa: BLE001 - 单台面板失败不能拖垮整页
            return None, {"vps_id": panel.id, "vps_name": panel.name, "error": str(exc)[:200]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for result, error in pool.map(load, panels):
            if error:
                errors.append(error)
                continue
            if result:
                items.extend(result["items"])

    # 匹配系统内的中转记录：入站被别人手工占用时 matched 为空，属于正常情况
    provisions = db.execute(
        select(TransitProvision.transit_vps_id, TransitProvision.inbound_id, TransitProvision.line_id,
               TransitProvision.status, CustomerLine.id, Customer.name, User.display_name)
        .join(CustomerLine, CustomerLine.id == TransitProvision.line_id, isouter=True)
        .join(Customer, Customer.id == CustomerLine.customer_id, isouter=True)
        .outerjoin(User, User.id == Customer.sales_id)
        .where(TransitProvision.transit_vps_id.in_([panel.id for panel in panels]))
    ).all()
    matched = {(row[0], row[1]): row for row in provisions}
    for item in items:
        row = matched.get((item["vps_id"], item["inbound_id"]))
        item["matched"] = None if row is None or row[4] is None else {
            "line_id": row[4], "customer_name": row[5], "sales_name": row[6], "status": row[3],
        }

    return {
        "items": items,
        "panels": [{"id": panel.id, "name": panel.name, "group_code": panel.group_code,
                    "connect_host": panel.connect_host, "is_enabled": panel.is_enabled} for panel in panels],
        "errors": errors,
    }


class InboundCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vps_id: int | None = None
    # 不传就检测这台面板上所有入站；出站可选，代理网关未必响应裸 TCP
    ports: list[int] = Field(default_factory=list, max_length=500)
    include_outbound: bool = False


@router.post("/transit-inbounds/check")
def check_transit_inbounds(payload: InboundCheckIn, db: Session = Depends(get_db),
                           user: User = Depends(require_roles("super_admin", "admin"))):
    query = select(TransitVps).where(TransitVps.is_enabled.is_(True))
    if payload.vps_id:
        query = select(TransitVps).where(TransitVps.id == payload.vps_id)
    panels = list(db.scalars(query.order_by(TransitVps.id)).all())

    def load(panel: TransitVps):
        try:
            return _panel_snapshot(panel, db), None
        except Exception as exc:  # noqa: BLE001
            return None, {"vps_id": panel.id, "vps_name": panel.name, "error": str(exc)[:200]}

    snapshots = []
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result, error in pool.map(load, panels):
            if error:
                errors.append(error)
            elif result:
                snapshots.append(result)

    targets: list[tuple[str, int, dict]] = []
    for snapshot in snapshots:
        for item in snapshot["items"]:
            if payload.ports and item["port"] not in payload.ports:
                continue
            targets.append((item["connect_host"], item["port"], {"kind": "inbound", **item}))
            if payload.include_outbound and item.get("exit_host") and item.get("exit_port"):
                targets.append((item["exit_host"], int(item["exit_port"]),
                                {"kind": "outbound", **item}))

    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        results = list(pool.map(lambda target: probe(target[0], target[1]), targets))

    checks = []
    for (host, port, meta), (ok, latency, reason) in zip(targets, results):
        checks.append({
            "kind": meta["kind"], "vps_name": meta["vps_name"], "inbound_id": meta["inbound_id"],
            "port": meta["port"], "host": host, "ok": ok, "latency_ms": latency, "error": reason,
            "outbound_tag": meta.get("outbound_tag"), "exit_host": meta.get("exit_host"),
        })
    return {
        "checked": len(checks),
        "healthy": sum(1 for item in checks if item["ok"]),
        "offline": sum(1 for item in checks if not item["ok"]),
        "checks": checks,
        "errors": errors,
    }
