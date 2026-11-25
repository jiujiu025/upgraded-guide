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
    CatalogCityOption,
    Customer,
    CustomerLine,
    Node,
    Order,
    Procurement,
    Product,
    Region,
    Supplier,
)
from app.services.audit_service import write_audit
from app.services.order_service import claim_delivery_slot, notify_order_parties
from app.services.settings_service import auto_procurement_enabled
from app.services.subscription_service import issue_token
from app.services.supplier_service import ensure_supplier
from app.services.transit_service import (
    TransitNotConfigured,
    TransitProvisionError,
    provision_transit,
    requires_transit,
)
from app.suppliers.base import SupplierError
from app.suppliers.factory import build_readonly_adapter
from app.suppliers.global_proxy import DAYS_TIMELEN

# 采购永远是买同一个东西：上游的静态住宅 IP。
# 三类产品线共用这一个货源，社媒/直播只是多一层中转（见 transit_service）。
PROCURABLE_CATEGORIES = {
    "static_residential", "social_media", "live_stream_mobile", "live_stream_pc",
}

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


def _ensure_placeholder_node(db: Session, *, server: str, port: int, protocol: str,
                             region_id: int, name: str) -> Node:
    """客户线路必须有 node_id。按 (server, port, 协议) 复用，避免一单一行。"""
    node = db.scalar(select(Node).where(
        Node.server == server,
        Node.port == port,
        Node.protocol == protocol,
        Node.region_id == region_id,
    ))
    if node is None:
        node = Node(name=name[:120], server=server, port=port,
                    protocol=protocol, region_id=region_id, is_enabled=True)
        db.add(node)
        db.flush()
    return node


def _link_provision(db: Session, order_id: int, line_index: int, line_id: int) -> None:
    """把中转记录关联到最终线路：后台重试时要能反查这条入站对应哪个客户。"""
    from app.models import TransitProvision

    record = db.scalar(select(TransitProvision).where(
        TransitProvision.order_id == order_id, TransitProvision.line_index == line_index,
    ))
    if record is not None:
        record.line_id = line_id


def _fail(db: Session, record: Procurement, code: str, message: str, customer: Customer | None,
          order: Order) -> Procurement:
    record.status = "failed"
    record.error_code = code
    record.error_message = message[:500]
    # 系统动作也要留痕：失败原因必须能在审计里查到，不能只躺在 procurements 表里
    write_audit(db, None, "procure_fail", "order", order.id, f"采购失败：{code} {message[:200]}")
    db.commit()
    if customer is not None:
        notify_order_parties(
            db, customer, order, "自动采购失败",
            f"订单 {order.order_no} 自动采购未成功（{message[:80]}），已转人工处理，无需重复付款。",
            "procurement",
        )
        db.commit()
    return record


def _create_customer_lines(
    db: Session,
    *,
    order: Order,
    customer: Customer | None,
    accounts: list,
    endpoints: list,
    region: Region,
    now: datetime,
    upstream_order_no: str,
) -> list[int]:
    """把采购到的账号变成客户线路。

    三类产品共用这里：静态住宅交付 socks5 直连，社媒/直播交付中转入口（vless），
    差别只在 ``endpoints`` 有没有值——这样两条路径不会长出两套交付逻辑。
    """
    settings = get_settings()
    public_base = (settings.frontend_origin or "").rstrip("/")
    token = issue_token(customer.id) if customer else None
    subscription = f"{public_base}/api/sub/{token}" if public_base and token else None
    created: list[int] = []
    for index, account in enumerate(accounts, start=1):
        if not claim_delivery_slot(db, order.id, now):
            break
        db.expire(order)
        db.refresh(order)
        expires_at = account.expires_at or (now + timedelta(days=order.billing_days))
        endpoint = endpoints[index - 1] if index <= len(endpoints) else None
        if endpoint is not None:
            # 社媒/直播交付的是**中转入口**，不是住宅 IP 本身：
            # 客户连 vless，出口才是刚买的住宅 IP，所以 static_ip 写出口而不是入口。
            link = endpoint.link or _build_node_link(account, region.name, index, len(accounts))
            node = _ensure_placeholder_node(
                db, server=endpoint.server, port=endpoint.port, protocol=endpoint.protocol,
                region_id=region.id, name=endpoint.remark or f"{region.name} 专线 {index}",
            )
            static_ip = endpoint.exit_ip or account.ip
        else:
            link = _build_node_link(account, region.name, index, len(accounts))
            node = _ensure_placeholder_node(
                db, server=account.ip or account.host or "", port=account.port or 0,
                protocol="socks5", region_id=region.id,
                name=f"{region.name} 静态IP {account.ip or ''}".strip(),
            )
            static_ip = account.ip
        line = CustomerLine(
            customer_id=order.customer_id, node_id=node.id, product_id=order.product_id,
            order_id=order.id, static_ip=static_ip, started_at=now, expires_at=expires_at,
            node_link=link,
            subscription_url=subscription,
            node_qr_data=link,
            subscription_qr_data=subscription,
            customer_note=f"上游单号 {upstream_order_no}",
        )
        db.add(line)
        db.flush()
        created.append(line.id)
        if endpoint is not None:
            _link_provision(db, order.id, index, line.id)
        if order.delivered_line_id is None:
            order.delivered_line_id = line.id
    return created


def auto_procure_order(db: Session, order: Order, *, trigger: str = "payment") -> Procurement | None:
    """付款成功后尝试自动采购。不满足条件时静默返回 None，不影响原有流程。"""
    # 后台开关优先（可随时掐断），没有记录才看环境变量
    if not auto_procurement_enabled(db):
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

    # 失败过的单子要能重试：唯一键是 (order_id, line_index)，不能新建第二行，
    # 复用同一行并把状态改回 pending。succeeded / pending 直接返回，不再下单。
    existing = db.scalar(select(Procurement).where(
        Procurement.order_id == order.id, Procurement.line_index == 0,
    ))
    if existing is not None:
        if existing.status in {"succeeded", "pending"}:
            return existing
        existing.status = "pending"
        existing.error_code = None
        existing.error_message = None
        existing.request_payload = json.dumps(request, ensure_ascii=False)
        db.commit()
        record = existing
    else:
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
        # 超时/限流都不重试：上游没有幂等键，重试等于重复扣款。
        # 上游原始响应落到 response_payload，运维才能判断是余额不足还是参数问题。
        payload = getattr(exc, "payload", None)
        if payload:
            record.response_payload = json.dumps(payload, ensure_ascii=False)[:2000]
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
    # 社媒/直播比静态住宅多一跳 VPS 中转：IP 买到了不等于能交付。
    # 中转没配好就停在 pending_transit —— 绝不能把裸 IP 当专线交给客户。
    # 注意这里不能走 _fail：IP 已经买了（钱已扣），标 failed 会诱导人工重试，
    # 重试就是再买一次。
    transit_endpoints: list = []
    if requires_transit(product.resource_category):
        try:
            transit_endpoints = provision_transit(
                db, order=order, accounts=result.accounts,
                country_code=region.country_code, city_name=request["city"],
            )
        except (TransitNotConfigured, TransitProvisionError) as exc:
            order.status = "pending_transit"
            order.confirmed_at = order.confirmed_at or now
            record.status = "awaiting_transit"
            record.error_code = getattr(exc, "code", "TRANSIT_NOT_READY")
            record.error_message = str(exc)[:500]
            write_audit(db, None, "procure_awaiting_transit", "order", order.id,
                        f"上游单号={result.order_number}; 原因={str(exc)[:200]}")
            db.commit()
            if customer is not None:
                notify_order_parties(
                    db, customer, order, "线路正在配置中转",
                    f"订单 {order.order_no} 的住宅 IP 已就绪，正在配置专线中转，配置完成后即可使用。",
                    "procurement",
                )
                db.commit()
            return record

    created = _create_customer_lines(
        db, order=order, customer=customer, accounts=list(result.accounts),
        endpoints=transit_endpoints, region=region, now=now,
        upstream_order_no=result.order_number,
    )

    record.status = "succeeded"
    record.line_id = created[0] if created else None
    write_audit(db, None, "procure_success", "order", order.id,
                f"上游单号={result.order_number}; 线路={','.join(str(item) for item in created)}")
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


def deliver_pending_transit(db: Session, order: Order, *, actor=None) -> list[int]:
    """待配中转的订单：补做中转并交付。给后台「重试配置中转」用。

    幂等：已 configured 的中转记录直接复用原链接；线路靠 ``claim_delivery_slot``
    原子占位，重复点击不会多建线路。
    """
    from types import SimpleNamespace

    from app.services.transit_service import retry_provision

    if order.status != "pending_transit":
        raise ValueError("订单不在待配中转状态")
    customer = db.scalar(select(Customer).where(
        Customer.id == order.customer_id, Customer.is_deleted.is_(False)))
    product = db.get(Product, order.product_id)
    region = db.get(Region, order.region_id or (product.region_id if product else 0))
    if customer is None or product is None or region is None:
        raise ValueError("客户、产品或地区不可用")

    endpoints = retry_provision(db, order=order)
    # 出口账密已经在上次配置时加密留档，这里只要占位对象跑通交付循环
    placeholders = [SimpleNamespace(expires_at=None, ip="", host="", port=0) for _ in endpoints]

    # claim_delivery_slot 只认 pending_delivery / partially_delivered：
    # 中转已经配好，这一步是真正可以交付了，才推进状态。
    order.status = "pending_delivery"
    db.flush()

    record = db.scalar(select(Procurement).where(
        Procurement.order_id == order.id, Procurement.line_index == 0))
    upstream_no = record.external_order_no if record and record.external_order_no else order.order_no
    created = _create_customer_lines(
        db, order=order, customer=customer, accounts=placeholders, endpoints=endpoints,
        region=region, now=_utcnow(), upstream_order_no=upstream_no,
    )

    if record is not None:
        record.status = "succeeded"
        record.error_code = None
        record.error_message = None
        if created:
            record.line_id = created[0]
    write_audit(db, actor, "deliver", "order", order.id,
                f"中转配置完成；线路={','.join(str(item) for item in created)}")
    db.commit()

    notify_order_parties(
        db, customer, order, "线路已开通",
        f"订单 {order.order_no} 的专线中转已配置完成，共 {len(created)} 条线路，"
        "可在「我的线路」查看连接信息。",
        "delivery",
    )
    db.commit()
    return created
