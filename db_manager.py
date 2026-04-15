import os
from supabase import create_client, Client
from datetime import datetime, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("WARNING: Supabase credentials missing. DB Operations will fail.")
    supabase = None

def get_us_date():
    """Converts GitHub's UTC server time to US Eastern Time."""
    return (datetime.utcnow() - timedelta(hours=5)).strftime("%Y-%m-%d")

def is_already_logged(matchup, market, selection):
    if not supabase: return False
    
    # Clean the strings and use case-insensitive matching to prevent duplicates
    c_matchup = matchup.strip()
    c_market = market.strip().upper()
    c_selection = selection.strip()

    res = supabase.table("bets_log")\
        .select("id")\
        .ilike("matchup", c_matchup)\
        .eq("market", c_market)\
        .ilike("selection", c_selection)\
        .eq("result", "")\
        .execute()
        
    return len(res.data) > 0

def log_bet_to_db(matchup, market, selection, odds, edge_val, units, fair_price):
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
        "closing_line_pinnacle": "",
        "result": ""
    }
    supabase.table("bets_log").insert(data).execute()

def get_open_clv_bets():
    if not supabase: return []
    res = supabase.table("bets_log").select("*").eq("closing_line_pinnacle", "").neq("date", "").execute()
    return res.data

def update_clv(bet_id, closing_line):
    if not supabase: return
    formatted_line = f"+{closing_line}" if float(closing_line) > 0 else str(closing_line)
    supabase.table("bets_log").update({"closing_line_pinnacle": formatted_line}).eq("id", bet_id).execute()

def get_ungraded_past_bets():
    if not supabase: return []
    res = supabase.table("bets_log").select("*").eq("result", "").lt("date", get_us_date()).execute()
    return res.data

def update_result(bet_id, result_str):
    if not supabase: return
    supabase.table("bets_log").update({"result": result_str}).eq("id", bet_id).execute()

def get_all_clv_bets():
    if not supabase: return []
    res = supabase.table("bets_log").select("*").neq("closing_line_pinnacle", "").execute()
    return res.data

def get_all_graded_bets():
    if not supabase: return []
    res = supabase.table("bets_log").select("*").in_("result", ["WIN", "LOSS"]).execute()
    return res.data

def get_all_bets():
    if not supabase: return []
    res = supabase.table("bets_log").select("*").execute()
    return res.data