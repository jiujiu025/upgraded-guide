"""采购记录：一次上游采购尝试的全貌。

`procurements` 表从自动采购上线起就一直在写，但此前**只能查库或走接口调试**——
出了「客户付了钱、上游没给 IP」这种事，管理员在后台看不到状态、上游单号、错误原因，
只能让开发去连生产库。自动采购是扣钱动作，这种可见性缺口不能留。

只读页面，两个接口：

- 列表：按状态 / 供应商 / 订单 / 关键词 / 时间筛选，带状态计数（分页后前端不能靠
  当页数据拼下拉框）；
- 详情：补上请求摘要（国家、城市、号段、数量），方便复现。

**刻意不返回 `request_payload` / `response_payload` 原文**：后者是上游返回的
IP、端口、账号、密码，前者虽是我们自己拼的，但一旦开放原文，就会有人把它当
日志接口用，迟早把凭据带到前端缓存里。详情只给白名单字段和账号数量。

重试不在这里做：扣款动作必须只有一条实现。前端拿 `order_id` 调
`POST /admin/orders/{id}/retry-procurement`（已收款且无成功记录才真下单）。
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, like_pattern
from app.db import get_db
from app.models import Customer, Order, Procurement, Supplier, User

router = APIRouter(prefix="/admin/procurements", tags=["采购记录"])

STATUSES = ("pending", "succeeded", "failed", "manual")
# 详情里允许回显的请求字段：都是我们自己拼的业务参数，不含任何凭据
REQUEST_FIELDS = ("order_no", "quantity", "billing_days", "country", "city", "segment", "trigger")


def _request_summary(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        # 历史数据可能是截断或非 JSON，不猜含义
        return None
    if not isinstance(payload, dict):
        return None
    return {key: payload[key] for key in REQUEST_FIELDS if key in payload}


def _account_count(raw: str | None) -> int:
    """上游成功返回时 response_payload 是账号数组，只回数量，不回账号内容。"""
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return 0
    return len(payload) if isinstance(payload, list) else 0


def _conditions(
    *,
    status: str | None,
    supplier_id: int | None,
    order_id: int | None,
    keyword: str | None,
    start: datetime | None,
    end: datetime | None,
) -> list:
    conditions = []
    if status:
        conditions.append(Procurement.status == status)
    if supplier_id:
        conditions.append(Procurement.supplier_id == supplier_id)
    if order_id:
        conditions.append(Procurement.order_id == order_id)
    if keyword and keyword.strip():
        pattern = like_pattern(keyword.strip())
        conditions.append(or_(
            Order.order_no.ilike(pattern),
            Procurement.external_order_no.ilike(pattern),
            Procurement.error_code.ilike(pattern),
            Procurement.error_message.ilike(pattern),
        ))
    if start is not None:
        conditions.append(Procurement.created_at >= start.replace(tzinfo=None))
    if end is not None:
        conditions.append(Procurement.created_at <= end.replace(tzinfo=None))
    return conditions


def serialize_procurement(
    record: Procurement,
    order_no: str | None,
    product_name: str | None,
    customer_name: str | None,
    supplier_name: str | None,
) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "status": record.status,
        "order_id": record.order_id,
        "order_no": order_no,
        "product_name": product_name,
        "customer_name": customer_name,
        "line_index": record.line_index,
        "supplier_id": record.supplier_id,
        "supplier_code": record.supplier_code,
        "supplier_name": supplier_name,
        "external_order_no": record.external_order_no,
        "error_code": record.error_code,
        "error_message": record.error_message,
        "line_id": record.line_id,
        "has_response": bool(record.response_payload),
    }


@router.get("")
def list_procurements(
    status: str | None = Query(default=None, pattern="^(pending|succeeded|failed|manual)$"),
    supplier_id: int | None = Query(default=None, ge=1),
    order_id: int | None = Query(default=None, ge=1),
    keyword: str | None = Query(default=None, max_length=80, description="订单号 / 上游单号 / 错误码"),
    start: datetime | None = Query(default=None, description="起始时间（含）"),
    end: datetime | None = Query(default=None, description="结束时间（含）"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    """分页查采购记录。

    全部用 outer join：订单可能被删（`order_id` 还在但查不到行），供应商也可能被
    清理，inner join 会让这些"最需要排查"的记录直接从列表里消失。
    """
    query = (
        select(Procurement, Order.order_no, Order.product_name, Customer.name, Supplier.name)
        .outerjoin(Order, Procurement.order_id == Order.id)
        .outerjoin(Customer, Order.customer_id == Customer.id)
        .outerjoin(Supplier, Procurement.supplier_id == Supplier.id)
    )
    conditions = _conditions(status=status, supplier_id=supplier_id, order_id=order_id,
                             keyword=keyword, start=start, end=end)
    if conditions:
        query = query.where(*conditions)

    total = db.scalar(
        select(func.count(Procurement.id))
        .select_from(Procurement)
        .outerjoin(Order, Procurement.order_id == Order.id)
        .where(*conditions)
    ) or 0

    rows = db.execute(
        query.order_by(Procurement.created_at.desc(), Procurement.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [serialize_procurement(record, order_no, product_name, customer_name, supplier_name)
             for record, order_no, product_name, customer_name, supplier_name in rows]

    # 状态计数用「除状态外的同一组筛选」，否则选中某个状态后下拉框只剩一个选项
    count_conditions = _conditions(status=None, supplier_id=supplier_id, order_id=order_id,
                                   keyword=keyword, start=start, end=end)
    counts = dict(db.execute(
        select(Procurement.status, func.count(Procurement.id))
        .select_from(Procurement)
        .outerjoin(Order, Procurement.order_id == Order.id)
        .where(*count_conditions)
        .group_by(Procurement.status)
    ).all())

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "counts": {name: counts.get(name, 0) for name in STATUSES},
        "suppliers": [
            {"id": row.id, "code": row.code, "name": row.name}
            for row in db.scalars(select(Supplier).order_by(Supplier.id))
        ],
    }


@router.get("/{procurement_id}")
def get_procurement(
    procurement_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    """单条详情：给排查用，不带凭据原文。"""
    row = db.execute(
        select(Procurement, Order.order_no, Order.product_name, Customer.name, Supplier.name)
        .outerjoin(Order, Procurement.order_id == Order.id)
        .outerjoin(Customer, Order.customer_id == Customer.id)
        .outerjoin(Supplier, Procurement.supplier_id == Supplier.id)
        .where(Procurement.id == procurement_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="采购记录不存在")

    record, order_no, product_name, customer_name, supplier_name = row
    detail = serialize_procurement(record, order_no, product_name, customer_name, supplier_name)
    detail.update({
        "request_summary": _request_summary(record.request_payload),
        "delivered_accounts": _account_count(record.response_payload),
    })
    return detail
