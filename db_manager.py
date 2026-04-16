import os
from supabase import create_client, Client
from datetime import datetime, timedelta

# Configuration & Initialization
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

def get_us_date():
    """Returns current date in US Central format (UTC-5)."""
    return (datetime.utcnow() - timedelta(hours=5)).strftime("%Y-%m-%d")

def is_already_logged(matchup, market, selection):
    """Prevents duplicate alerts by checking for open bets with the same parameters."""
    if not supabase: return False
    res = supabase.table("bets_log").select("id").ilike("matchup", matchup.strip())\
        .eq("market", market.strip().upper()).ilike("selection", selection.strip())\
        .eq("result", "").execute()
    return len(res.data) > 0

def log_bet_to_db(matchup, market, selection, odds, edge_val, units, fair_price, sport, event_id):
    """Inserts a new bet into the Supabase ledger with full metadata."""
    if not supabase: return
    data = {
        "date": get_us_date(),
        "matchup": matchup.strip(),
        "market": market.upper().strip(),
        "selection": selection.strip(),
        "odds": str(odds),
        "edge": f"{float(edge_val)*100:.2f}%" if isinstance(edge_val, float) else str(edge_val),
        "units": str(units),
        "fair_price": str(fair_price),
        "sport": sport,
        "event_id": str(event_id),
        "closing_line_pinnacle": "",
        "result": ""
    }
    supabase.table("bets_log").insert(data).execute()

def get_ungraded_past_bets():
    """Fetches bets from previous days that do not yet have a WIN/LOSS result."""
    if not supabase: return []
    today = get_us_date()
    res = supabase.table("bets_log").select("*").eq("result", "").lt("date", today).execute()
    return res.data

def get_untracked_bets():
    """Fetches bets missing Pinnacle closing line data for CLV analysis."""
    if not supabase: return []
    res = supabase.table("bets_log").select("*").eq("closing_line_pinnacle", "").execute()
    return res.data

def update_result(bet_id, result):
    """Updates a bet with its final outcome (WIN, LOSS, PUSH)."""
    if not supabase: return
    supabase.table("bets_log").update({"result": result}).eq("id", bet_id).execute()

def update_bet_clv(bet_id, closing_price, clv_edge):
    """Updates a bet with the final sharp market price and calculated CLV."""
    if not supabase: return
    supabase.table("bets_log").update({
        "closing_line_pinnacle": str(closing_price),
        "edge": f"{float(clv_edge)*100:.2f}%"
    }).eq("id", bet_id).execute()

def get_all_graded_bets():
    """Retrieves all finished bets for accounting and P/L reporting."""
    if not supabase: return []
    res = supabase.table("bets_log").select("*").neq("result", "").execute()
    return res.data

def get_all_clv_bets():
    """Retrieves all bets that have a tracked closing line for CLV analysis."""
    if not supabase: return []
    res = supabase.table("bets_log").select("*").neq("closing_line_pinnacle", "").execute()
    return res.data

def get_all_bets():
    """Retrieves the entire betting history."""
    if not supabase: return []
    res = supabase.table("bets_log").select("*").execute()
    return res.data

def save_master_cache(cache_data):
    """Pushes the master odds dictionary to the Supabase JSONB column."""
    if not supabase: 
        print("Supabase client not initialized.")
        return
    
    # We use 'upsert' with a hardcoded ID of 'master'. 
    # This ensures we are always overwriting the same row rather than creating millions of rows.
    try:
        supabase.table("odds_cache").upsert({
            "id": "master",
            "data": cache_data,
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        print(f"Error saving cache to Supabase: {e}")

def get_master_cache():
    """Pulls the live master odds dictionary from Supabase."""
    if not supabase: return {}
    
    try:
        res = supabase.table("odds_cache").select("data").eq("id", "master").execute()
        if res.data:
            return res.data[0]["data"]
        return {}
    except Exception as e:
        print(f"Error loading cache from Supabase: {e}")
        return {}