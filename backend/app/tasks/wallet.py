"""钱包账本巡检任务。

CLI（``scripts/audit_wallet_ledger.py``）适合上线前手工跑一次；上了生产之后，
账是持续变化的，靠人记得去跑等于没有检查。这里把它做成定时任务：

- 每轮跑一次只读巡检，结果计数写进 ``scheduled_task_runs``（可在后台任务页看到曲线）；
- **只有阻断项才发通知**。提示项（浮点副本分叉、孤儿余额）在过渡期是常态，
  天天推只会让人忽略通知；
- 按自然日去重，同一管理员当天只收到一条，账没修好第二天继续提醒。
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.services.wallet_audit_service import audit_wallet_ledger
from app.tasks.notify import notify_admins

# 通知里最多列这么多条明细，其余归到"另有 N 项"，避免一条通知刷满屏。
MAX_DETAIL_LINES = 5


def audit_wallet(db: Session, *, now: datetime, detail_limit: int = 50) -> dict:
    report = audit_wallet_ledger(db, detail_limit=detail_limit)
    blocking = report.blocking_issues
    counts = {
        "blocking": len(blocking),
        "warnings": len(report.warnings),
        "accounts": report.accounts,
        "transactions": report.transactions,
        "customers": report.customers,
    }
    # 只把非零的分类带上，否则每轮都是一串 0，看不出变化。
    counts.update({kind: value for kind, value in report.stats.items() if value})

    notified = 0
    if blocking:
        day_marker = f"巡检日期 {now:%Y-%m-%d}"
        lines = [f"- {item.detail}" for item in blocking[:MAX_DETAIL_LINES]]
        if len(blocking) > MAX_DETAIL_LINES:
            lines.append(f"- 另有 {len(blocking) - MAX_DETAIL_LINES} 项未列出")
        notified = notify_admins(
            db,
            "钱包账本巡检发现阻断问题",
            (
                f"本轮发现 {len(blocking)} 项阻断问题，说明账本身不自洽，"
                f"请先核对再继续任何金额相关操作：\n" + "\n".join(lines) +
                f"\n（{day_marker}）"
            ),
            "finance",
            marker=day_marker,
        )

    return {**counts, "admins_notified": notified}
