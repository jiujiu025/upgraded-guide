"""付款后自动向上游采购静态住宅，并把拿到的代理账号直接交付给客户。

为什么要单独一个服务，而不是塞进交付接口：

- 上游下单是**扣钱**的动作，必须有幂等键（procurements 的 order_id + line_index），
  重复触发只能真实下单一次；
- 上游没有幂等键，失败**不能自动重试**，只能转人工——否则一次超时就是两次扣款；
- 采购成功后要走和手工交付一样的线路生成逻辑，否则客户看到的东西会分成两套。

交付给客户的是代理账号（IP + 端口 + 账号 + 密码），拼成 socks5:// 链接，
因此二维码与 Clash 订阅可以直接复用。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    CatalogCityOption, Customer, CustomerLine, Node, Order, Procurement, Product, Region, Supplier,
)
from app.services.order_service import claim_delivery_slot, notify_order_parties
from app.services.subscription_service import issue_token
from app.services.supplier_service import ensure_supplier
from app.suppliers.base import SupplierError
from app.suppliers.factory import build_readonly_adapter
from app.suppliers.global_proxy import DAYS_TIMELEN

# 只有这些分类能自动采购：上游卖的是静态住宅（原生），其他产品线没有对应接口。
PROCURABLE_CATEGORIES = {"static_residential"}

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _supplier_of(db: Session) -> Supplier | None:
    return ensure_supplier(db, "quanqiudaili")


def _city_name(db: Session, order: Order) -> str | None:
    """客户选了城市码时要换成上游认的城市名（上游按名字下单，不认我们的码）。"""
    code = (order.requested_city_code or "").strip()
    if not code:
        return None
    region = db.get(Region, order.region_id) if order.region_id else None
    if region is None:
        return None
    row = db.scalar(select(CatalogCityOption).where(
        CatalogCityOption.country_code == region.country_code,
        CatalogCityOption.city_code == code,
    ))
    return row.city_name if row else code


def _build_node_link(account, region_name: str, index: int, quantity: int) -> str:
    """把上游代理账号拼成 socks5 分享链接，客户端与订阅都按这一条走。"""
    host = account.ip or account.host or ""
    label = f"{region_name}-静态IP" + (f"-{index}" if quantity > 1 else "")
    from urllib.parse import quote

    credential = quote(f"{account.username}:{account.password}", safe=":@")
    return f"socks5://{credential}@{host}:{account.port or 0}#{quote(label)}"


def _ensure_placeholder_node(db: Session, account, region_id: int, name: str) -> Node:
    """客户线路必须有 node_id。按 (server, port, socks5) 复用，避免一单一行。"""
    host = account.ip or account.host or ""
    port = account.port or 0
    node = db.scalar(select(Node).where(
        Node.server == host,
        Node.port == port,
        Node.protocol == "socks5",
        Node.region_id == region_id,
    ))
    if node is None:
        node = Node(name=name[:120], server=host, port=port,
                    protocol="socks5", region_id=region_id, is_enabled=True)
        db.add(node)
        db.flush()
    return node


def _fail(db: Session, record: Procurement, code: str, message: str, customer: Customer | None,
          order: Order) -> Procurement:
    record.status = "failed"
    record.error_code = code
    record.error_message = message[:500]
    db.commit()
    if customer is not None:
        notify_order_parties(
            db, customer, order, "自动采购失败",
            f"订单 {order.order_no} 自动采购未成功（{message[:80]}），已转人工处理，无需重复付款。",
            "procurement",
        )
        db.commit()
    return record


def auto_procure_order(db: Session, order: Order, *, trigger: str = "payment") -> Procurement | None:
    """付款成功后尝试自动采购。不满足条件时静默返回 None，不影响原有流程。"""
    settings = get_settings()
    if not settings.supplier_auto_procurement_enabled:
        return None
    if order.order_type != "purchase" or order.source_type == "resource_pool":
        return None
    product = db.get(Product, order.product_id)
    if product is None or (product.resource_category or "") not in PROCURABLE_CATEGORIES:
        return None
    region = db.get(Region, order.region_id or product.region_id)
    if region is None or not region.country_code:
        return None
    # 已经买成功过就直接返回，避免重复下单（放在状态检查之前：交付完的订单也要能查到记录）
    done = db.scalar(select(Procurement).where(
        Procurement.order_id == order.id, Procurement.status == "succeeded",
    ))
    if done is not None:
        return done
    # 余额支付成功直接把订单置成 paid；交付占位只认 pending_delivery，这里推进一步。
    if order.status == "paid":
        order.status = "pending_delivery"
        order.confirmed_at = order.confirmed_at or _utcnow()
        db.commit()
    elif order.status not in {"pending_delivery", "partially_delivered"}:
        return None

    customer = db.scalar(select(Customer).where(Customer.id == order.customer_id, Customer.is_deleted.is_(False)))
    supplier = _supplier_of(db)
    if supplier is None:
        return None

    timelen = DAYS_TIMELEN.get(order.billing_days)
    request = {
        "order_no": order.order_no,
        "quantity": order.quantity,
        "billing_days": order.billing_days,
        "country": region.country_code,
        "city": _city_name(db, order),
        "segment": order.requested_ip_segment or None,
        "trigger": trigger,
    }
    if timelen is None:
        record = Procurement(order_id=order.id, line_index=0, supplier_id=supplier.id,
                             supplier_code=supplier.code, status="manual",
                             request_payload=json.dumps(request, ensure_ascii=False),
                             error_code="UNSUPPORTED_PERIOD",
                             error_message=f"上游没有 {order.billing_days} 天档位")
        db.add(record)
        db.commit()
        return record

    record = Procurement(
        order_id=order.id, line_index=0, supplier_id=supplier.id, supplier_code=supplier.code,
        status="pending", request_payload=json.dumps(request, ensure_ascii=False),
    )
    try:
        db.add(record)
        db.commit()
    except IntegrityError:
        # 并发触发：另一个请求已经在买，直接读它的结果
        db.rollback()
        return db.scalar(select(Procurement).where(
            Procurement.order_id == order.id, Procurement.line_index == 0,
        ))

    adapter = build_readonly_adapter(db, supplier)
    segments = None
    if order.requested_ip_segment and "." in order.requested_ip_segment:
        segments = [(order.requested_ip_segment.strip(), order.quantity)]
    try:
        result = adapter.buy_static_ip(
            quantity=order.quantity,
            timelen=timelen,
            country_code=region.country_code,
            city=request["city"],
            segments=segments,
            remark=order.order_no,
        )
    except SupplierError as exc:
        # 超时/限流都不重试：上游没有幂等键，重试等于重复扣款
        return _fail(db, record, getattr(exc, "code", "SUPPLIER_ERROR"), str(exc), customer, order)
    except Exception as exc:  # noqa: BLE001 - 采购是外部调用，任何异常都要落库转人工
        return _fail(db, record, "UNEXPECTED", f"{type(exc).__name__}: {exc}", customer, order)

    record.external_order_no = result.order_number
    record.response_payload = json.dumps(
        [{"ip": account.ip, "port": account.port, "username": account.username,
          "expires_at": account.expires_at.isoformat() if account.expires_at else None}
         for account in result.accounts], ensure_ascii=False,
    )

    now = _utcnow()
    public_base = (settings.frontend_origin or "").rstrip("/")
    token = issue_token(customer.id) if customer else None
    created: list[int] = []
    for index, account in enumerate(result.accounts, start=1):
        if not claim_delivery_slot(db, order.id, now):
            break
        db.expire(order)
        db.refresh(order)
        expires_at = account.expires_at or (now + timedelta(days=order.billing_days))
        link = _build_node_link(account, region.name, index, len(result.accounts))
        node = _ensure_placeholder_node(db, account, region.id, f"{region.name} 静态IP {account.ip or ''}".strip())
        line = CustomerLine(
            customer_id=order.customer_id, node_id=node.id, product_id=order.product_id,
            order_id=order.id, static_ip=account.ip, started_at=now, expires_at=expires_at,
            node_link=link,
            subscription_url=f"{public_base}/api/sub/{token}" if public_base and token else None,
            node_qr_data=link,
            subscription_qr_data=f"{public_base}/api/sub/{token}" if public_base and token else None,
            customer_note=f"上游单号 {result.order_number}",
        )
        db.add(line)
        db.flush()
        created.append(line.id)
        if order.delivered_line_id is None:
            order.delivered_line_id = line.id

    record.status = "succeeded"
    record.line_id = created[0] if created else None
    db.commit()

    if customer is not None:
        notify_order_parties(
            db, customer, order, "线路已自动交付",
            f"订单 {order.order_no} 已自动开通 {len(created)} 条线路，可在「我的线路」查看 IP 与连接信息。",
            "delivery",
        )
        db.commit()
    return record


def run_auto_procure_in_background(order_id: int) -> None:
    """后台执行采购，不阻塞支付响应。

    上游下单最慢会卡到超时（默认 10 秒），放在支付请求里会让客户以为付款失败、
    重复点支付。这里用独立 session，异常只记日志——采购结果本身落在 procurements
    表里，后台能看到，不会因为日志丢了就查不到。
    """
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        order = db.get(Order, order_id)
        if order is None:
            return
        auto_procure_order(db, order, trigger="payment")
    except Exception:  # noqa: BLE001 - 后台任务不能把异常抛给调用方
        db.rollback()
        logger.exception("auto procure failed order_id=%s", order_id)
    finally:
        db.close()
