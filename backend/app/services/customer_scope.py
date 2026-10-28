from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, User


def get_customer_profile(db: Session, user: User) -> Customer:
    """返回当前客户账号绑定的有效客户档案。"""
    customer = db.scalar(select(Customer).where(Customer.user_id == user.id, Customer.is_deleted.is_(False)))
    if not customer:
        raise HTTPException(status_code=404, detail="客户档案未绑定")
    return customer


def scoped_customer_ids(db: Session, user: User):
    if user.role in {"super_admin", "admin"}:
        return select(Customer.id).where(Customer.is_deleted.is_(False))
    if user.role == "sales":
        return select(Customer.id).where(Customer.sales_id == user.id, Customer.is_deleted.is_(False))
    customer = get_customer_profile(db, user)
    return select(Customer.id).where(Customer.id == customer.id)
