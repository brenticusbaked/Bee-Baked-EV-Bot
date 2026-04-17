import os
from datetime import datetime
from zoneinfo import ZoneInfo


DEFAULT_TZ = os.getenv("BEE_BAKED_TIMEZONE", "America/Chicago")


def get_local_now() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TZ))


def get_local_date_str() -> str:
    return get_local_now().strftime("%Y-%m-%d")
