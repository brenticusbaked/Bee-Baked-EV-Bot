"""Fire the odds-cache-ingest Edge Function at pipeline startup.

The scanner cache is populated by the Supabase Edge Function on a pg_cron
schedule (game hours only). A pipeline run that lands OUTSIDE that schedule
(manual runs, off-hours) reads odds older than the freshness cutoff and sees an
empty cache. Triggering a fresh, forced ingest right before ``hydrate_market_cache``
guarantees the cache is current whenever ``master_run`` executes, at the cost of
one small ingest. Best-effort: any failure is logged and the pipeline continues
on the previous snapshot (never raises).
"""
import os
from typing import Any, Dict

import requests


def _function_url() -> str:
    url = (os.getenv("ODDS_INGEST_FUNCTION_URL") or "").strip()
    if url:
        return url
    base = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/functions/v1/odds-cache-ingest"


def trigger_odds_ingest() -> Dict[str, Any]:
    """POST a forced ingest to the Edge Function and summarise the result.

    Returns a pipeline-task dict (``detail``/``count``/``label``) so the runner
    prints a ``[ok] trigger_odds_ingest: ...`` line. Never raises.
    """
    url = _function_url()
    if not url:
        return {"detail": "skipped: no ODDS_INGEST_FUNCTION_URL/SUPABASE_URL", "count": 0, "label": "ingest"}

    secret = (os.getenv("ODDS_INGEST_FUNCTION_SECRET") or "").strip()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["x-ingest-secret"] = secret

    timeout = float(os.getenv("ODDS_INGEST_TRIGGER_TIMEOUT", "60"))
    try:
        resp = requests.post(
            url,
            json={"trigger": "manual_pipeline", "force": True},
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:
        return {"detail": f"trigger failed ({type(exc).__name__}); kept previous cache", "count": 0, "label": "ingest"}

    if resp.status_code != 200:
        # Body may carry a helpful reason; it never contains the ingest secret
        # (that is sent only in the request header).
        body = ""
        try:
            body = (resp.text or "")[:200]
        except Exception:
            body = ""
        return {"detail": f"trigger HTTP {resp.status_code}; kept previous cache | body={body}", "count": 0, "label": "ingest"}

    try:
        data = resp.json()
    except Exception:
        return {"detail": "trigger ok (non-JSON response)", "count": 0, "label": "ingest"}

    status = data.get("status", "ok")
    odds_rows = int(data.get("oddsRows", 0) or 0)
    fixtures = int(data.get("fixtures", 0) or 0)
    remaining = data.get("remaining")
    detail = (
        f"ingest {status}: {odds_rows} odds rows, {fixtures} fixtures upserted"
        + (f", ~{remaining} credits left" if remaining is not None else "")
    )
    return {"detail": detail, "count": odds_rows, "label": "ingest"}
