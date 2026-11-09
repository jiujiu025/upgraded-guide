"""财务导出接口。

这个文件只做三件事：校验区间、拼文件名、把行流出去。所有口径都在
`finance_export_service` 里，接口层不碰金额。

不开成普通 JSON 接口的原因：一个月的钱包流水几千行，JSON 序列化后会占几十兆内存，
而财务要的是能直接丢进 Excel 的文件。流式响应边查边发，内存占用和行数无关。
"""

from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.api.deps import require_roles
from app.db import get_db
from app.models import User
from app.services.audit_service import write_audit
from app.services.finance_export_service import (
    KIND_TITLES, ExportError, columns_of, iter_csv, iter_rows, resolve_range,
)


router = APIRouter(tags=["财务中心与钱包"])

# BOM 必须在最前面：没有它，Excel 会把 UTF-8 的中文按 GBK 解读成乱码。
# 这是财务导出最常见的抱怨，代价只有一个字节。
_BOM = "\ufeff"


def _audit_quietly(db: Session, actor: User, kind: str, start_day: date, end_day: date, rows: int) -> None:
    """导出是数据外流动作，必须留痕。

    放在 finally 里是因为「导出了一半然后断开」同样意味着数据已经到了对方手上，
    不记就无法追溯。审计写失败也绝不能影响已经发出的文件，所以整体吞掉异常。
    """
    try:
        write_audit(db, actor, "export_finance", "finance_export", kind,
                    detail=f"区间={start_day.isoformat()}~{end_day.isoformat()}; 行数={rows}")
        db.commit()
    except Exception:  # noqa: BLE001 - 审计失败不该把已经发出去的文件变成报错
        db.rollback()


def _csv_body(db: Session, actor: User, kind: str, start_day: date, end_day: date) -> Iterator[str]:
    total = 0
    first = True
    try:
        for chunk, total in iter_csv(kind, iter_rows(db, kind, start_day, end_day)):
            yield (_BOM + chunk) if first else chunk
            first = False
    finally:
        _audit_quietly(db, actor, kind, start_day, end_day, total)


@router.get("/admin/finance/exports/{kind}")
def export_finance_csv(
    kind: str,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    try:
        start_day, end_day = resolve_range(date_from, date_to)
        columns_of(kind)
    except ExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    filename = f"finance-{kind}-{start_day:%Y%m%d}-{end_day:%Y%m%d}.csv"
    return StreamingResponse(
        _csv_body(db, actor, kind, start_day, end_day),
        media_type="text/csv; charset=utf-8",
        headers={
            # 纯 ASCII 文件名，避免 Content-Disposition 的 RFC 5987 编码在旧客户端上失效。
            "Content-Disposition": f'attachment; filename="{filename}"',
            # 财务数据不进任何缓存。
            "Cache-Control": "no-store",
        },
    )


@router.get("/admin/finance/exports")
def list_finance_exports(_: User = Depends(require_roles("super_admin", "admin"))):
    """给前端渲染导出按钮用：有哪些报表、各自是什么口径。"""
    return [
        {"kind": "orders", "title": KIND_TITLES["orders"],
         "basis": "按下单时间归属；含未付款订单，实收列仅在已支付时填写"},
        {"kind": "payments", "title": KIND_TITLES["payments"],
         "basis": "按支付时间归属；退款列显示该支付单的退款情况"},
        {"kind": "wallet", "title": KIND_TITLES["wallet"],
         "basis": "按记账时间归属；含充值、消费、退款与期初余额"},
        {"kind": "monthly", "title": KIND_TITLES["monthly"],
         "basis": "按自然月汇总；只统计线上与线下真实现金，余额消费单列"},
    ]
