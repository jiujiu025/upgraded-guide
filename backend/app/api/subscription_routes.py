"""客户订阅接口：Clash / v2rayN / 小火箭 直接拉取。

订阅地址是客户级的，token 无状态签名（见 subscription_service），所以这里不加登录态——
Clash 拉配置时不会带 Cookie。token 泄露等于订阅泄露，因此：
- 只返回节点连接信息，不返回客户姓名、余额、订单等任何业务数据；
- 客户被删除或线路全部到期时，返回空配置而不是报错，客户端不会一直弹红。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, CustomerLine
from app.services.qr_service import qr_svg_bytes
from app.services.subscription_service import (
    build_base64_subscription, build_clash_config, parse_token, wants_clash,
)

router = APIRouter(prefix="/sub", tags=["subscription"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _active_lines(db: Session, customer_id: int) -> list[CustomerLine]:
    """只给到期的、启用中的线路；到期但状态还没被定时任务刷新的，也按到期时间挡掉。"""
    now = _utcnow()
    return list(db.scalars(
        select(CustomerLine)
        .where(
            CustomerLine.customer_id == customer_id,
            CustomerLine.status == "active",
            CustomerLine.expires_at > now,
        )
        .order_by(CustomerLine.id)
    ).all())


def _customer_or_none(db: Session, token: str) -> Customer | None:
    customer_id = parse_token(token)
    if customer_id is None:
        return None
    return db.scalar(select(Customer).where(
        Customer.id == customer_id, Customer.is_deleted.is_(False),
    ))


@router.get("/{token}")
def get_subscription(token: str, request: Request, target: str | None = None,
                     db: Session = Depends(get_db)):
    customer = _customer_or_none(db, token)
    if customer is None:
        return Response("# 订阅地址无效或客户已停用\n", status_code=404,
                        media_type="text/plain; charset=utf-8")
    links = [line.node_link or "" for line in _active_lines(db, customer.id)]
    headers = {
        # 客户端默认 12 小时拉一次；线路变更后最多半天同步到客户设备
        "Profile-Update-Interval": "12",
        "Cache-Control": "no-store",
    }
    if wants_clash(request.headers.get("user-agent", ""), target):
        return Response(build_clash_config(links), media_type="text/yaml; charset=utf-8",
                        headers=headers)
    return Response(build_base64_subscription(links), media_type="text/plain; charset=utf-8",
                    headers=headers)


@router.get("/{token}/qr.svg")
def subscription_qr(token: str, request: Request, db: Session = Depends(get_db)):
    """订阅地址本身的二维码：手机扫码直接进客户端。"""
    customer = _customer_or_none(db, token)
    if customer is None:
        return Response(status_code=404)
    url = str(request.url).split("?", 1)[0]
    return Response(qr_svg_bytes(url, scale=5), media_type="image/svg+xml")


@router.get("/{token}/node/{line_id}.svg")
def node_qr(token: str, line_id: int, db: Session = Depends(get_db)):
    """单条线路的节点链接二维码。"""
    customer = _customer_or_none(db, token)
    if customer is None:
        return Response(status_code=404)
    line = db.scalar(select(CustomerLine).where(
        CustomerLine.id == line_id, CustomerLine.customer_id == customer.id,
    ))
    if line is None or not line.node_link:
        return Response(status_code=404)
    return Response(qr_svg_bytes(line.node_link, scale=5), media_type="image/svg+xml")
