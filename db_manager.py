import os
from supabase import create_client, Client
from datetime import datetime, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

def get_us_date():
    return (datetime.utcnow() - timedelta(hours=5)).strftime("%Y-%m-%d")

def is_already_logged(matchup, market, selection):
    if not supabase: return False
    res = supabase.table("bets_log").select("id").ilike("matchup", matchup.strip())\
        .eq("market", market.strip().upper()).ilike("selection", selection.strip()).eq("result", "").execute()
    return len(res.data) > 0

def log_bet_to_db(matchup, market, selection, odds, edge_val, units, fair_price, sport, event_id):
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