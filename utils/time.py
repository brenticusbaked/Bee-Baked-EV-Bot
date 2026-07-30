import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TZ = os.getenv("BEE_BAKED_TIMEZONE", "America/Chicago")


def get_local_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(DEFAULT_TZ))
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone()


def get_local_date_str() -> str:
    return get_local_now().strftime("%Y-%m-%d")
