import asyncio
import os
from datetime import datetime
from services.alerts import send_discord_alert

# Import your individual book scrapers
from scraper_draftkings import scrape_dk
from scraper_fanduel import scrape_fd
from scraper_betmgm import scrape_mgm
from scraper_prizepicks import scrape_pp

DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL")

async def run_scrapers_async():
    """Runs all book scrapers in parallel with increased timeouts."""
    print(f"BEE-BAKED SCRAPER PIPELINE STARTING - {datetime.utcnow().isoformat()}")
    
    # We run these concurrently to save GitHub Action minutes
    tasks = [
        scrape_dk(),
        scrape_fd(),
        scrape_mgm(),
        scrape_pp()
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results for the status report
    summary = []
    for i, res in enumerate(results):
        name = ["DraftKings", "FanDuel", "BetMGM", "PrizePicks"][i]
        if isinstance(res, Exception):
            summary.append(f"❌ {name}: {str(res)[:50]}...")
        else:
            summary.append(f"✅ {name}: Success")
            
    return summary

def run_scraper_pipeline():
    """Sync wrapper to execute the async pipeline."""
    loop = asyncio.get_event_loop()
    summary_list = loop.run_until_complete(run_scrapers_async())
    
    # Send a quick health check to your Discord Status channel
    report = "**Scraper Pipeline Report**\n" + "\n".join(summary_list)
    if DISCORD_STATUS_WEBHOOK_URL:
        send_discord_alert({"content": report}, webhook_url=DISCORD_STATUS_WEBHOOK_URL)
    
    print("BEE-BAKED SCRAPER PIPELINE COMPLETE.")
