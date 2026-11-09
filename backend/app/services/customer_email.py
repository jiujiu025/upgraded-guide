"""客户资料的跨表规则。

邮箱唯一：`customers.email` 没有唯一约束，注册与后台创建都允许重复，客户合并和
通知去重时会踩坑。这里做应用层校验（不做数据库唯一索引：存量数据可能已有重复，
加索引会让迁移直接失败，而且软删除的客户不该继续占位）。
"""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    normalized = email.strip().lower()
    return normalized or None


def ensure_unique_customer_email(db: Session, email: str | None, exclude_id: int | None = None) -> None:
    """同一邮箱只能属于一个未删除客户；大小写与首尾空格视为同一个。"""
    normalized = normalize_email(email)
    if not normalized:
        return
    query = select(Customer.id).where(
        func.lower(func.trim(Customer.email)) == normalized,
        Customer.is_deleted.is_(False),
    )
    if exclude_id is not None:
        query = query.where(Customer.id != exclude_id)
    if db.scalar(query) is not None:
        raise HTTPException(status_code=400, detail="该邮箱已被其他客户使用，请核对后再提交")
