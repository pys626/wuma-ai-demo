"""Beijing wall-clock time for stored teaching records, independent of server TZ."""
from datetime import datetime, timedelta, timezone

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_beijing():
    """Keep the existing naive timestamp format, consistently in UTC+8."""
    return datetime.now(BEIJING_TIMEZONE).replace(tzinfo=None)
