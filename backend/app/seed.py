from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import Node, Product, Region, User


def seed() -> None:
    settings = get_settings()
    if settings.environment not in {"development", "test"}:
        raise ValueError("种子脚本只允许在开发或测试环境运行")
    if not settings.admin_password:
        raise ValueError("执行种子脚本前必须通过 ADMIN_PASSWORD 配置管理员密码")
    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.username == settings.admin_username))
        if not admin:
            admin = User(username=settings.admin_username, password_hash=hash_password(settings.admin_password), role="super_admin", display_name="超级管理员")
            db.add(admin)
        region = db.scalar(select(Region).where(Region.name == "香港"))
        if not region:
            region = Region(name="香港", country_code="HK")
            db.add(region)
            db.flush()
        product = db.scalar(select(Product).where(Product.name == "香港直播专线"))
        if not product:
            db.add(Product(name="香港直播专线", product_type="直播线路", price=199, billing_days=30, region_id=region.id))
        node = db.scalar(select(Node).where(Node.name == "香港边缘节点-01"))
        if not node:
            db.add(Node(name="香港边缘节点-01", server="1.1.1.1", port=443, protocol="VLESS", region_id=region.id))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
