from typing import Optional

from db_manager import log_alert_event
from services.http_client import post_discord


def send_discord_alert(
    payload: dict,
    source: str,
    alert_type: str,
    dedupe_key: Optional[str] = None,
    webhook_url: Optional[str] = None,
    add_bee_image: bool = False,
) -> bool:
    sent = post_discord(payload, webhook_url=webhook_url, add_bee_image=add_bee_image)
    if sent:
        preview = ""
        embeds = payload.get("embeds", [])
        if embeds:
            preview = str(embeds[-1].get("description", ""))[:500]
        elif "content" in payload:
            preview = str(payload.get("content", ""))[:500]
        log_alert_event(
            source=source,
            alert_type=alert_type,
            dedupe_key=dedupe_key,
            count=1,
            payload_preview=preview,
            status="sent",
        )
    return sent
