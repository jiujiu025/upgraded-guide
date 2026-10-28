import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, InboxThread, User
from app.services.customer_scope import get_customer_profile


THREAD_STATUSES = {"open", "pending", "closed"}
SENSITIVE_MESSAGE_PATTERNS = (
    re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(密码|令牌|密钥|订阅链接)\s*[:：=]\s*\S+"),
    re.compile(r"(?i)\b(vless|vmess|trojan|ss|ssr)://\S+"),
    re.compile(r"(?i)https?://\S+"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b"),
)


def validate_safe_text(content: str) -> str:
    """拒绝可能包含密码、Token、订阅链接或节点凭据的正文。"""
    normalized = content.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    if any(pattern.search(normalized) for pattern in SENSITIVE_MESSAGE_PATTERNS):
        raise HTTPException(status_code=422, detail="消息不得包含密码、Token、订阅链接或节点凭据")
    return normalized


def scoped_inbox_threads(db: Session, user: User):
    query = select(InboxThread).join(Customer, Customer.id == InboxThread.customer_id)
    if user.role in {"super_admin", "admin"}:
        return query
    if user.role == "sales":
        return query.where(Customer.sales_id == user.id, Customer.is_deleted.is_(False))
    if user.role == "customer":
        customer = get_customer_profile(db, user)
        return query.where(InboxThread.customer_id == customer.id)
    raise HTTPException(status_code=403, detail="没有访问收件箱的权限")


def get_scoped_thread(db: Session, user: User, thread_id: int) -> InboxThread:
    thread = db.scalar(scoped_inbox_threads(db, user).where(InboxThread.id == thread_id))
    if not thread:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    return thread
