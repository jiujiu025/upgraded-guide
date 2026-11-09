"""列表分页的统一入口。

订单类接口原来一律 ``.all()`` 全表返回：客户看自己的几十单没问题，管理员看的是
**全站所有订单**，上万单之后首屏查询会超时，而且前端一次要渲染几千个卡片。

这里只做两件事，避免每个端点各写一遍 offset/limit 和 count：

- ``paginate``：限制当页条数并给出总数（总数用于"第 N / M 页"）；
- ``counts_by``：按某一列分组计数，用于状态筛选下拉框与看板徽标。

``counts_by`` 是必需的：分页之后前端只拿着当页的 25 条，不能再靠"当页里有什么状态"
去拼下拉框，否则老状态会在翻页后凭空消失。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def paginate(db: Session, query, page: int, page_size: int) -> tuple[list, int]:
    """返回 (当页记录, 满足条件的总条数)。"""
    # 计数用去掉排序的子查询：ORDER BY 对计数没有意义，某些方言还会因为
    # 排序列不在选择列表里报错。
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return list(items), total


def counts_by(db: Session, query, column_name: str) -> dict[str, int]:
    """按 ``column_name`` 分组计数，忽略空值。

    传进来的 ``query`` 应当**不含**该列的筛选条件——筛选后的分布会让下拉框
    在选中某个状态后只剩一个选项。
    """
    subquery = query.order_by(None).subquery()
    column = subquery.c[column_name]
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {value: count for value, count in rows if value}


def page_payload(items: list, total: int, page: int, page_size: int, **extra) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size, **extra}


def like_pattern(raw: str) -> str:
    """把用户输入包成 LIKE 模式，并转义 ``%`` ``_`` ``\\``。

    不转义的话，搜索框里输入一个 ``%`` 就等于"不过滤"，输入 ``_`` 会变成任意单字符
    通配——用户看到的结果和他输入的字面量对不上。
    """
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
