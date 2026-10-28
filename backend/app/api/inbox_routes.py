from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_roles
from app.db import get_db
from app.models import Customer, CustomerLine, InboxMessage, InboxThread, Notification, Order, User
from app.schemas.common import (
    InboxMessageIn,
    InboxMessageOut,
    InboxMessagePageOut,
    InboxSalesOptionOut,
    InboxThreadCreateIn,
    InboxThreadOut,
    InboxThreadPageOut,
    InboxThreadUpdateIn,
    InboxUnreadCountOut,
)
from app.services.audit_service import write_audit
from app.services.customer_scope import get_customer_profile
from app.services.inbox_service import THREAD_STATUSES, get_scoped_thread, scoped_inbox_threads, validate_safe_text
from app.services.order_service import add_notification

router = APIRouter(prefix="/inbox", tags=["收件箱"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def message_payload(db: Session, message: InboxMessage) -> dict:
    sender = db.get(User, message.sender_user_id)
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "sender_role": message.sender_role,
        "sender_display_name": sender.display_name if sender else "未知用户",
        "content": message.content,
        "customer_read_at": message.customer_read_at,
        "staff_read_at": message.staff_read_at,
        "created_at": message.created_at,
    }


def thread_payloads(db: Session, threads: list[InboxThread]) -> list[dict]:
    if not threads:
        return []
    customer_ids = {thread.customer_id for thread in threads}
    sales_ids = {thread.assigned_sales_id for thread in threads if thread.assigned_sales_id is not None}
    customer_names = dict(db.execute(
        select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
    ).all())
    sales_names = dict(db.execute(
        select(User.id, User.display_name).where(User.id.in_(sales_ids))
    ).all()) if sales_ids else {}
    return [
        {
            "id": thread.id,
            "customer_id": thread.customer_id,
            "customer_name": customer_names.get(thread.customer_id, "未知客户"),
            "assigned_sales_id": thread.assigned_sales_id,
            "assigned_sales_name": sales_names.get(thread.assigned_sales_id),
            "subject": thread.subject,
            "status": thread.status,
            "related_order_id": thread.related_order_id,
            "related_line_id": thread.related_line_id,
            "last_message_at": thread.last_message_at,
            "customer_unread_count": thread.customer_unread_count,
            "staff_unread_count": thread.staff_unread_count,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
        }
        for thread in threads
    ]


def thread_payload(db: Session, thread: InboxThread) -> dict:
    return thread_payloads(db, [thread])[0]


@router.post("/threads", response_model=InboxThreadOut, status_code=201)
def create_thread(
    payload: InboxThreadCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="只有客户可以创建会话")
    customer = get_customer_profile(db, user)
    subject = validate_safe_text(payload.subject)
    if payload.related_order_id and not db.scalar(select(Order.id).where(
        Order.id == payload.related_order_id,
        Order.customer_id == customer.id,
    )):
        raise HTTPException(status_code=422, detail="关联订单无效")
    if payload.related_line_id and not db.scalar(select(CustomerLine.id).where(
        CustomerLine.id == payload.related_line_id,
        CustomerLine.customer_id == customer.id,
    )):
        raise HTTPException(status_code=422, detail="关联线路无效")
    now = utcnow()
    try:
        thread = InboxThread(
            customer_id=customer.id,
            assigned_sales_id=customer.sales_id,
            subject=subject,
            status="open",
            related_order_id=payload.related_order_id,
            related_line_id=payload.related_line_id,
            last_message_at=now,
        )
        db.add(thread)
        db.flush()
        write_audit(db, user, "create", "inbox_thread", thread.id, "客户创建会话")
        db.commit()
        db.refresh(thread)
        return thread_payload(db, thread)
    except Exception:
        db.rollback()
        raise


@router.get("/threads", response_model=InboxThreadPageOut)
def list_threads(
    status: str | None = None,
    customer_id: int | None = None,
    assigned_sales_id: int | None = None,
    order_id: int | None = None,
    line_id: int | None = None,
    keyword: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if status and status not in THREAD_STATUSES:
        raise HTTPException(status_code=422, detail="会话状态不合法")
    if user.role == "customer" and (customer_id is not None or assigned_sales_id is not None):
        raise HTTPException(status_code=403, detail="客户不能指定数据归属筛选条件")
    query = scoped_inbox_threads(db, user)
    if status:
        query = query.where(InboxThread.status == status)
    if customer_id is not None:
        query = query.where(InboxThread.customer_id == customer_id)
    if assigned_sales_id is not None:
        query = query.where(InboxThread.assigned_sales_id == assigned_sales_id)
    if order_id is not None:
        query = query.where(InboxThread.related_order_id == order_id)
    if line_id is not None:
        query = query.where(InboxThread.related_line_id == line_id)
    if keyword:
        normalized_keyword = keyword.strip()
        message_match = select(InboxMessage.id).where(
            InboxMessage.thread_id == InboxThread.id,
            InboxMessage.content.contains(normalized_keyword),
        ).exists()
        query = query.where(or_(InboxThread.subject.contains(normalized_keyword), message_match))
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = list(db.scalars(
        query.order_by(InboxThread.last_message_at.desc(), InboxThread.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all())
    return {"items": thread_payloads(db, items), "total": total, "page": page, "page_size": page_size}


@router.get("/unread-count", response_model=InboxUnreadCountOut)
def get_unread_count(db: Session = Depends(get_db), user: User = Depends(current_user)):
    scoped = scoped_inbox_threads(db, user).order_by(None).subquery()
    unread_column = scoped.c.customer_unread_count if user.role == "customer" else scoped.c.staff_unread_count
    unread_count = db.scalar(select(func.coalesce(func.sum(unread_column), 0)).select_from(scoped)) or 0
    return {"unread_count": int(unread_count)}


@router.get("/threads/{thread_id}", response_model=InboxThreadOut)
def get_thread(thread_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return thread_payload(db, get_scoped_thread(db, user, thread_id))


@router.get("/threads/{thread_id}/sales-options", response_model=list[InboxSalesOptionOut])
def get_thread_sales_options(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "super_admin")),
):
    thread = get_scoped_thread(db, user, thread_id)
    sales = db.scalar(
        select(User)
        .join(Customer, Customer.sales_id == User.id)
        .where(
            Customer.id == thread.customer_id,
            Customer.is_deleted.is_(False),
            User.role == "sales",
            User.is_active.is_(True),
        )
    )
    return [] if sales is None else [{"id": sales.id, "display_name": sales.display_name}]


@router.get("/threads/{thread_id}/messages", response_model=InboxMessagePageOut)
def list_messages(
    thread_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    get_scoped_thread(db, user, thread_id)
    total = db.scalar(select(func.count()).select_from(InboxMessage).where(InboxMessage.thread_id == thread_id)) or 0
    messages = db.scalars(
        select(InboxMessage)
        .where(InboxMessage.thread_id == thread_id)
        .order_by(InboxMessage.created_at, InboxMessage.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [message_payload(db, message) for message in messages],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/threads/{thread_id}/messages", response_model=InboxMessageOut, status_code=201)
def send_message(
    thread_id: int,
    payload: InboxMessageIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    thread = get_scoped_thread(db, user, thread_id)
    if thread.status == "closed":
        raise HTTPException(status_code=409, detail="已关闭会话不能发送消息")
    content = validate_safe_text(payload.content)
    now = utcnow()
    customer_side = user.role == "customer"
    try:
        message = InboxMessage(
            thread_id=thread.id,
            sender_user_id=user.id,
            sender_role=user.role,
            content=content,
            customer_read_at=now if customer_side else None,
            staff_read_at=None if customer_side else now,
        )
        db.add(message)
        db.flush()
        values = {"last_message_at": now}
        if customer_side:
            values["staff_unread_count"] = InboxThread.staff_unread_count + 1
        else:
            values["customer_unread_count"] = InboxThread.customer_unread_count + 1
        db.execute(update(InboxThread).where(InboxThread.id == thread.id).values(**values))
        write_audit(db, user, "send_message", "inbox_thread", thread.id, f"消息={message.id}")
        customer = db.get(Customer, thread.customer_id)
        if customer_side:
            if thread.assigned_sales_id:
                add_notification(db, thread.assigned_sales_id, "收件箱有新消息", "你负责的客户会话收到一条新消息。", "inbox")
            else:
                admin_ids = db.scalars(select(User.id).where(User.role.in_({"admin", "super_admin"}), User.is_active.is_(True))).all()
                for admin_id in admin_ids:
                    add_notification(db, admin_id, "收件箱有待处理消息", "一个未分配的客户会话收到新消息。", "inbox")
        elif customer:
            add_notification(db, customer.user_id, "收件箱有新回复", "你的客户会话收到一条新回复。", "inbox")
        db.commit()
        db.refresh(message)
        return message_payload(db, message)
    except Exception:
        db.rollback()
        raise


@router.post("/threads/{thread_id}/read")
def mark_thread_read(thread_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    thread = get_scoped_thread(db, user, thread_id)
    now = utcnow()
    customer_side = user.role == "customer"
    try:
        if customer_side:
            result = db.execute(update(InboxMessage).where(
                InboxMessage.thread_id == thread.id,
                InboxMessage.customer_read_at.is_(None),
            ).values(customer_read_at=now))
            thread.customer_unread_count = 0
        else:
            result = db.execute(update(InboxMessage).where(
                InboxMessage.thread_id == thread.id,
                InboxMessage.staff_read_at.is_(None),
            ).values(staff_read_at=now))
            thread.staff_unread_count = 0
        write_audit(db, user, "mark_read", "inbox_thread", thread.id, f"已读消息数={result.rowcount}")
        db.commit()
        return {"ok": True, "updated": result.rowcount}
    except Exception:
        db.rollback()
        raise


@router.patch("/threads/{thread_id}", response_model=InboxThreadOut)
def update_thread(
    thread_id: int,
    payload: InboxThreadUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.role not in {"sales", "admin", "super_admin"}:
        raise HTTPException(status_code=403, detail="没有修改会话的权限")
    thread = get_scoped_thread(db, user, thread_id)
    changed: list[str] = []
    if "status" in payload.model_fields_set:
        if payload.status not in THREAD_STATUSES:
            raise HTTPException(status_code=422, detail="会话状态不合法")
        if thread.status != payload.status:
            thread.status = payload.status
            changed.append(f"状态={payload.status}")
    if "assigned_sales_id" in payload.model_fields_set:
        if user.role not in {"admin", "super_admin"}:
            raise HTTPException(status_code=403, detail="只有管理员可以调整会话负责人")
        if payload.assigned_sales_id is not None:
            sales = db.scalar(select(User).join(Customer, Customer.sales_id == User.id).where(
                User.id == payload.assigned_sales_id,
                User.role == "sales",
                User.is_active.is_(True),
                Customer.id == thread.customer_id,
                Customer.is_deleted.is_(False),
            ))
            if not sales:
                raise HTTPException(status_code=422, detail="负责人必须是该客户当前的有效销售")
        if thread.assigned_sales_id != payload.assigned_sales_id:
            thread.assigned_sales_id = payload.assigned_sales_id
            changed.append(f"负责人={payload.assigned_sales_id}")
    if not changed:
        raise HTTPException(status_code=422, detail="没有可更新的会话字段")
    try:
        write_audit(db, user, "update", "inbox_thread", thread.id, "; ".join(changed))
        db.commit()
        db.refresh(thread)
        return thread_payload(db, thread)
    except Exception:
        db.rollback()
        raise
