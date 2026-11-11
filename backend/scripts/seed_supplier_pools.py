"""初始化两家上游与两个资源池（幂等）。

    python scripts/seed_supplier_pools.py            # 预演
    python scripts/seed_supplier_pools.py --apply    # 写入

产物：
- 资源池 1（global_proxy_pool）  ← 全球代理（quanqiudaili，只读适配器）
- 资源池 2（global_proxy_pool_2）← 985Proxy（985proxy，983 预下单/支付/轮询）

只建骨架，不写商品/国家/库存：那些必须由各自的上游同步任务写入，
否则两家数据会混在一起（同步函数按 supplier_id 隔离，见 docs/双上游资源池设计.md）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ResourcePool, ResourcePoolSupplier, Supplier
from app.services.supplier_service import (
    SUPPLIER_CODE, ensure_pool, ensure_pool_one, ensure_supplier, link_pool_supplier,
)

POOL_TWO_CODE = "global_proxy_pool_2"
POOL_TWO_NAME = "资源池 2"
SECOND_SUPPLIER_CODE = "985proxy"


def run(apply: bool) -> int:
    db = SessionLocal()
    try:
        before = {
            "suppliers": db.scalar(select(Supplier.id).where(Supplier.code == SUPPLIER_CODE)) is not None,
            "pools": db.scalar(select(ResourcePool.id).where(ResourcePool.code == POOL_TWO_CODE)) is not None,
        }
        print(f"资源池 1 / {SUPPLIER_CODE}: {'已存在' if before['suppliers'] else '将创建'}")
        print(f"资源池 2 / {SECOND_SUPPLIER_CODE}: {'已存在' if before['pools'] else '将创建'}")
        if not apply:
            print("\n预演完成，加 --apply 写入")
            return 0

        supplier_one, pool_one = ensure_pool_one(db)
        supplier_two = ensure_supplier(db, SECOND_SUPPLIER_CODE)
        pool_two = ensure_pool(db, POOL_TWO_CODE, name=POOL_TWO_NAME)
        link_pool_supplier(db, pool_two, supplier_two, priority=1)
        db.commit()

        for pool in (pool_one, pool_two):
            members = db.execute(
                select(ResourcePoolSupplier, Supplier)
                .join(Supplier, Supplier.id == ResourcePoolSupplier.supplier_id)
                .where(ResourcePoolSupplier.pool_id == pool.id)
                .order_by(ResourcePoolSupplier.priority)
            ).all()
            names = "、".join(f"{supplier.name}（优先级 {member.priority}）" for member, supplier in members)
            print(f"✓ {pool.name}：{names}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化双上游资源池骨架（幂等）")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库")
    raise SystemExit(run(parser.parse_args().apply))
