import os
from supabase import create_client, Client
from datetime import datetime

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("WARNING: Supabase credentials missing. DB Operations will fail.")
    supabase = None

def is_already_logged(matchup, market, selection):
    if not supabase: return False
    
    # 1. Clean the strings (removes accidental spaces from sportsbooks)
    c_matchup = matchup.strip()
    c_market = market.strip().upper()
    c_selection = selection.strip()

    # 2. Use ILIKE (case-insensitive) and check for UNGRADED bets
    # This completely bypasses the GitHub UTC Timezone bug!
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
        "date": datetime.now().strftime("%Y-%m-%d"),
        "matchup": matchup,
        "market": market.upper(),
        "selection": selection,
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
    today = datetime.now().strftime("%Y-%m-%d")
    res = supabase.table("bets_log").select("*").eq("result", "").lt("date", today).execute()
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