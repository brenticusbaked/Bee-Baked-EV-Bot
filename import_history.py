import os
import pandas as pd
from db_manager import supabase

def decimal_to_american(decimal_odds):
    if pd.isna(decimal_odds) or decimal_odds <= 1.0:
        return "-110"
    if decimal_odds >= 2.0:
        return f"+{int(round((decimal_odds - 1.0) * 100))}"
    else:
        return f"{int(round(-100 / (decimal_odds - 1.0)))}"

def import_csv():
    if not supabase:
        print("[import] Supabase client not configured.")
        return

    # Check if history is already imported
    try:
        existing = supabase.table("bets_log").select("id", count="exact").ilike("notes", "%Historical import%").limit(1).execute()
        if existing.count and existing.count > 0:
            print(f"[import] Historical data already detected in bets_log ({existing.count} records). Skipping import.")
            return
    except Exception as e:
        print(f"[import] Could not verify existing history, proceeding cautiously: {e}")

    # Robust multi-location path resolution for bet_history.csv
    possible_paths = [
        "bet_history.csv",
        os.path.join(os.getcwd(), "bet_history.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bet_history.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bet_history.csv")
    ]
    
    csv_path = None
    for path in possible_paths:
        if os.path.exists(path):
            csv_path = path
            break
            
    if not csv_path:
        print(f"Error: bet_history.csv could not be found in any search path: {possible_paths}")
        return
        
    print(f"Reading bet_history.csv from: {csv_path}")
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"Error reading CSV at {csv_path}: {e}")
        return

    settled = df[df['status'].isin(['SETTLED_WIN', 'SETTLED_LOSS', 'SETTLED_PUSH', 'SETTLED_VOID'])].copy()
    
    status_map = {
        'SETTLED_WIN': 'win',
        'SETTLED_LOSS': 'loss',
        'SETTLED_PUSH': 'push',
        'SETTLED_VOID': 'push'
    }
    
    rows_to_insert = []
    for _, row in settled.iterrows():
        raw_amount = float(row['amount']) if pd.notna(row['amount']) else 0.0
        
        rows_to_insert.append({
            "matchup": "Historical Wager",
            "selection": str(row['bet_info'])[:200] if pd.notna(row['bet_info']) else f"Wager ({row['bet_id']})",
            "market": str(row['type']).upper() if pd.notna(row['type']) else "UNKNOWN",
            "odds": decimal_to_american(row['odds']),
            "edge": float(row['ev']) if pd.notna(row['ev']) else 0.0,
            "units": round(raw_amount / 3.0, 2),
            "sport": str(row['sports']).lower() if pd.notna(row['sports']) else "unknown",
            "result": status_map[row['status']],
            "date": row['time_placed_iso'],
            "graded_at": row['time_settled_iso'],
            "notes": f"Historical import - ID: {row['bet_id']}"
        })
        
    print(f"Found {len(rows_to_insert)} settled bets to import.")

    chunk_size = 1000
    for i in range(0, len(rows_to_insert), chunk_size):
        chunk = rows_to_insert[i:i + chunk_size]
        try:
            supabase.table("bets_log").insert(chunk).execute()
            print(f"Inserted {min(i + chunk_size, len(rows_to_insert))} / {len(rows_to_insert)} rows...")
        except Exception as e:
            print(f"Failed to insert chunk starting at {i}: {e}")
            
    print("Historical import complete! Monte Carlo is ready.")

if __name__ == "__main__":
    import_csv()