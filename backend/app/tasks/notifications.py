"""通知分发任务：把窗口内未投递的站内信推到已配置的渠道。"""

from sqlalchemy.orm import Session

from app.services.notification_channel_service import (
    DEGRADE_THRESHOLD, DISPATCH_MARKER, _degraded_channel_ids, dispatch_pending_notifications,
)
from app.tasks.notify import notify_admins


def notification_dispatch(db: Session, now) -> dict:
    """执行分发并汇报。

    渠道连续失败达到阈值时给管理员发一条带 DISPATCH_MARKER 的站内告警——
    该标记会让这条告警本身不再进入分发（防风暴环）。
    """
    degraded_before = _degraded_channel_ids(db)
    counts = dispatch_pending_notifications(db, now)
    degraded_after = _degraded_channel_ids(db)
    if degraded_after - degraded_before:
        notify_admins(
            db,
            "通知渠道连续失败已降频",
            f"以下通知渠道连续失败达到 {DEGRADE_THRESHOLD} 条，已暂停推送，请到后台检查渠道配置："
            f"渠道 ID {sorted(degraded_after - degraded_before)}。{DISPATCH_MARKER}",
            "system",
            marker="[notify-dispatch]",
        )
    return counts
