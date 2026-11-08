"""统一的中国时区口径。

库里所有时间列都是 **naive UTC**（`utcnow()` 去掉时区信息后直接存），
而运营和财务看的是上海本地时间，日报、月报、导出都按上海的自然日切分。

这些换算以前散在各个路由里手写，写错一次就会让「本月收入」差一天。
集中在这里，调用方只表达意图，不再自己组合 `datetime.combine + astimezone`。
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def to_local(value: datetime) -> datetime:
    """naive UTC → 上海本地时间（带时区）。已带时区的值按原时区换算。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI)


def local_day_start_utc(day: date) -> datetime:
    """本地某天 00:00 对应的 naive UTC，用于和库里的时间列比较。"""
    return datetime.combine(day, time.min, SHANGHAI).astimezone(timezone.utc).replace(tzinfo=None)


def local_day_range_utc(start_day: date, end_day: date) -> tuple[datetime, datetime]:
    """本地 ``[start_day 00:00, end_day 次日 00:00)`` 对应的 UTC 左闭右开区间。"""
    return local_day_start_utc(start_day), local_day_start_utc(end_day + timedelta(days=1))


def local_date_of(value: datetime) -> date:
    """某个 UTC 时刻落在上海时区的哪一天。"""
    return to_local(value).date()


def local_month_of(value: datetime) -> str:
    """某个 UTC 时刻落在上海时区的哪个月，返回 ``YYYY-MM``。"""
    return to_local(value).strftime("%Y-%m")


def local_stamp(value: datetime | None) -> str:
    """导出用的本地时间文本；空值导出为空串而不是 "None"。"""
    return to_local(value).strftime("%Y-%m-%d %H:%M:%S") if value else ""


def month_keys(start_day: date, end_day: date) -> list[str]:
    """区间覆盖到的月份列表，用于月报补零。"""
    cursor = date(start_day.year, start_day.month, 1)
    keys: list[str] = []
    while cursor <= end_day:
        keys.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return keys
