from datetime import UTC, datetime


def db_utc_now() -> datetime:
    """返回数据库 DateTime 列使用的无时区 UTC 时间，供写入和比较使用。"""
    return datetime.now(UTC).replace(tzinfo=None)
