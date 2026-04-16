import os
from supabase import create_client, Client
from datetime import datetime, timedelta

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

def get_us_date():
    return (datetime.utcnow() - timedelta(hours=5)).strftime("%Y-%m-%d")

def is_already_logged(matchup, market, selection):
    if not supabase: return False
    res = supabase.table("bets_log").select("id").ilike("matchup", matchup.strip())\
        .eq("market", market.strip().upper()).ilike("selection", selection.strip())\
        .eq("result", "").execute()
    return len(res.data) > 0

def log_bet_to_db(matchup, market, selection, odds, edge_val, units, fair_price, sport, event_id):
    if not supabase: return
    data = {
        "date": get_us_date(), "matchup": matchup.strip(), "market": market.upper().strip(),
        "selection": selection.strip(), "odds": str(odds), "edge": f"{float(edge_val)*100:.2f}%",
        "units": str(units), "fair_price": str(fair_price), "sport": sport,
        "event_id": str(event_id), "closing_line_pinnacle": "", "result": ""
    }
    supabase.table("bets_log").insert(data).execute()

def get_ungraded_past_bets():
    if not supabase: return []
    today = get_us_date()
    res = supabase.table("bets_log").select("*").eq("result", "").lt("date", today).execute()
    return res.data

def get_untracked_bets():
    if not supabase: return []
    res = supabase.table("bets_log").select("*").eq("closing_line_pinnacle", "").execute()
    return res.data

def update_result(bet_id, result):
    if not supabase: return
    supabase.table("bets_log").update({"result": result}).eq("id", bet_id).execute()

def update_bet_clv(bet_id, closing_price, clv_edge):
    if not supabase: return
    supabase.table("bets_log").update({"closing_line_pinnacle": str(closing_price), "edge": f"{float(clv_edge)*100:.2f}%"}).eq("id", bet_id).execute()

def save_master_cache(cache_data):
    if not supabase: return
    try:
        supabase.table("odds_cache").upsert({
            "id": "master", "data": cache_data, "updated_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e: print(f"Error saving cache: {e}")

def get_master_cache():
    if not supabase: return {}
    try:
        res = supabase.table("odds_cache").select("data").eq("id", "master").execute()
        return res.data[0]["data"] if res.data else {}
    except Exception as e: print(f"Error loading cache: {e}"); return {}