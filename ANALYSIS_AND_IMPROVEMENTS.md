# Bee Baked Bot - Code Analysis & Improvement Plan

**Analysis Date:** May 17, 2026  
**Status:** Multiple issues identified, fixes provided below

---

## 🔴 Critical Issues

### 1. **Odds Edge Alert Workflow - HTTP 403 Forbidden**
**Severity:** 🔴 CRITICAL  
**Files:** `.github/workflows/odds_edge_alert.yml`, `odds_edge_alert.py`  
**Issue:** Discord webhook URL is invalid/expired/revoked  
**Last Failure:** 13 hours ago (May 16 21:07 UTC), recurring

**Error Log:**
```
odds_edge_alert failed: HTTP Error 403: Forbidden
```

**Root Cause:**
- The `DISCORD_WEBHOOK_URL` secret is invalid, expired, or the Discord server revoked it
- No error handling for 403 responses before raising exception

**Fix:**
1. Update Discord webhook URL in GitHub Secrets (Settings → Secrets and variables → Actions)
2. Ensure webhook URL format: `https://discordapp.com/api/webhooks/YOUR_ID/YOUR_TOKEN`
3. Verify webhook still exists in Discord server

---

## ⚠️ Code Quality Issues

### 2. **No Retry Logic for Discord Webhooks**
**Severity:** ⚠️ MEDIUM  
**Files:** `odds_edge_alert.py`, `db_manager.py`  
**Issue:** Discord webhook calls fail immediately without retries

**Current Code (odds_edge_alert.py:243):**
```python
with urllib.request.urlopen(request, timeout=15) as response:
    if response.status >= 300:
        raise RuntimeError(f"Discord webhook returned HTTP {response.status}")
```

**Problem:** No retry mechanism for transient errors (rate limits, timeouts)

**Improvement:**
```python
import time

def send_discord_update(webhook_url, alerts, threshold, rows_scanned, note=None):
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            payload = {
                "content": f"Odds edge update: {len(alerts)} opportunity(s) at or above {threshold * 100:.2f}%",
                "embeds": [{"title": "Odds Edge Update", "description": description}],
            }
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status >= 300:
                    if 500 <= response.status < 600 and attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    raise RuntimeError(f"Discord webhook returned HTTP {response.status}")
                return  # Success
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            if attempt < max_retries - 1:
                print(f"Discord webhook attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying...")
                time.sleep(retry_delay)
            else:
                raise RuntimeError(f"Discord webhook failed after {max_retries} attempts: {exc}")
```

---

### 3. **Missing Webhook URL Validation**
**Severity:** ⚠️ MEDIUM  
**Files:** `odds_edge_alert.py`, `unified_bot.py`  
**Issue:** No validation that webhook URL exists before attempting to send

**Current:**
```python
if args.webhook_url and (alerts or args.always_notify):
    send_discord_update(args.webhook_url, alerts, args.threshold, len(rows), note=note)
```

**Problem:** Webhook could be None, empty, or invalid - only fails at send time

**Improvement:**
```python
def validate_webhook_url(url):
    """Validate Discord webhook URL format."""
    if not url or not isinstance(url, str):
        return False
    return url.startswith("https://discordapp.com/api/webhooks/") or \
           url.startswith("https://discord.com/api/webhooks/")

if args.webhook_url:
    if not validate_webhook_url(args.webhook_url):
        print(f"WARNING: Invalid Discord webhook URL format: {args.webhook_url[:30]}...", file=sys.stderr)
        sys.exit(1)
    if alerts or args.always_notify:
        try:
            send_discord_update(args.webhook_url, alerts, args.threshold, len(rows), note=note)
        except Exception as exc:
            print(f"Discord notification failed (webhook may be expired): {exc}", file=sys.stderr)
            # Still exit successfully since analysis completed
```

---

### 4. **Bare Exception Handlers**
**Severity:** ⚠️ MEDIUM  
**Files:** Multiple (db_manager.py, scraper_*.py, model_*.py)  
**Issue:** Catch-all `except Exception` without specific error handling

**Examples:**
```python
# scraper_prizepicks.py:150
except Exception as exc:
    print(f"PrizePicks browser-context fetch failed: {exc}")
    return {"detail": f"prizepicks scrape error: {exc}", "count": 0, "label": "alerts"}

# db_manager.py:35
except Exception as exc:
    print(f"Supabase operation failed: {exc}")
    return fallback
```

**Problem:** Masks programming errors, makes debugging difficult

**Recommendation:**
```python
import logging

logger = logging.getLogger(__name__)

try:
    # code
except requests.HTTPError as exc:
    logger.error(f"HTTP error: {exc.response.status_code}")
    return fallback
except requests.Timeout:
    logger.warning("Request timeout, will retry")
    return fallback
except Exception as exc:
    logger.exception(f"Unexpected error: {exc}")
    raise
```

---

### 5. **Missing Type Hints**
**Severity:** 🔵 LOW  
**Files:** `master_run.py`, `scraper_*.py`  
**Issue:** Functions lack type annotations for better code clarity

**Before:**
```python
def _scrape_sport_news(sport: str):
    alerts = []
    response = request("GET", _feed_url(sport), headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
```

**After:**
```python
from typing import List, Dict, Any

def _scrape_sport_news(sport: str) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    response = request("GET", _feed_url(sport), headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
```

---

### 6. **Incomplete Requirements.txt - Pinned Versions Missing**
**Severity:** ⚠️ MEDIUM  
**File:** `requirements.txt`  
**Issue:** Dependencies lack version pinning, risking breaking changes

**Current:**
```
pandas
playwright>=1.45.0
requests
supabase
python-dotenv
pybaseball
```

**Issue:** Future major versions could break compatibility

**Improvement:**
```
pandas==2.0.3
playwright>=1.45.0,<2.0.0
requests==2.31.0
supabase==2.0.1
python-dotenv==1.0.0
pybaseball==0.22.0
```

---

### 7. **Bare `raise SystemExit(1)` in Error Handlers**
**Severity:** ⚠️ MEDIUM  
**File:** `odds_edge_alert.py:287`

**Current:**
```python
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"odds_edge_alert failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
```

**Problem:** Confusing error raising logic

**Better:**
```python
if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as exc:
        print(f"odds_edge_alert failed: {exc}", file=sys.stderr)
        sys.exit(1)
```

---

### 8. **No Logging Configuration**
**Severity:** ⚠️ MEDIUM  
**Files:** All  
**Issue:** Only uses `print()`, no structured logging for debugging

**Missing:**
- Log levels (DEBUG, INFO, WARNING, ERROR)
- Log rotation
- Timestamped logs
- Log aggregation support

**Recommendation:** Add logging.conf or use logging module:
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"Starting {sport} model...")
```

---

## 🟢 Workflow Improvements

### 9. **GitHub Actions Timeout Not Set**
**Severity:** 🔵 LOW  
**File:** `.github/workflows/main.yml`

**Add timeout-minutes to prevent hung workflows:**
```yaml
jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    timeout-minutes: 60  # Add this
    steps:
```

---

### 10. **Missing Error Notifications**
**Severity:** ⚠️ MEDIUM  
**Files:** GitHub Actions workflows

**Issue:** Failures don't always notify Discord

**Add to workflows:**
```yaml
- name: Notify Discord on Failure
  if: failure()
  run: |
    python -c "
    import urllib.request, json
    webhook = '${{ secrets.DISCORD_STATUS_WEBHOOK_URL }}'
    if webhook:
        payload = {'content': '❌ ${{ github.workflow }} failed', 'embeds': [{'description': 'Check: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}', 'color': 16711680}]}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(webhook, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=10)
    "
```

---

## 📊 Summary

| Issue | Severity | Impact | Fix Effort |
|-------|----------|--------|-----------|
| Discord Webhook 403 | 🔴 Critical | Bot can't send odds alerts | 5 mins |
| No Webhook Retry | ⚠️ Medium | Transient failures kill alerts | 15 mins |
| No URL Validation | ⚠️ Medium | Silent failures | 10 mins |
| Bare Exception Handlers | ⚠️ Medium | Hard to debug | 30 mins |
| Missing Type Hints | 🔵 Low | Code clarity | 1 hour |
| Unpinned Dependencies | ⚠️ Medium | Reproducibility | 20 mins |
| No Logging | ⚠️ Medium | Debugging difficult | 45 mins |
| Missing Workflow Timeout | 🔵 Low | Hung workflows possible | 5 mins |
| No Error Notifications | ⚠️ Medium | Silent failures | 10 mins |

---

## ✅ Next Steps

1. **IMMEDIATE (Today):**
   - [ ] Fix Discord webhook URL in GitHub Secrets
   - [ ] Re-run Odds Edge Alert workflow
   - [ ] Verify webhook is valid in Discord server

2. **SHORT TERM (This Week):**
   - [ ] Add retry logic to Discord webhook calls
   - [ ] Implement webhook URL validation
   - [ ] Add timeout-minutes to all workflows
   - [ ] Pin dependency versions

3. **MEDIUM TERM (This Month):**
   - [ ] Add structured logging
   - [ ] Replace bare exceptions with specific error types
   - [ ] Add type hints to all functions
   - [ ] Add Discord failure notifications to workflows

4. **LONG TERM:**
   - [ ] Add unit tests for error scenarios
   - [ ] Implement monitoring/alerting dashboard
   - [ ] Add health checks for external APIs

