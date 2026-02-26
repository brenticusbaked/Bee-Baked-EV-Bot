import json
import os
import sys
import time
import asyncio
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

# --- SECURE CREDENTIAL LOADING ---
load_dotenv()

ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')

if not ODDS_API_KEY or not DISCORD_WEBHOOK_URL:
    print("❌ Error: Missing API Key or Webhook URL. Check your GitHub Secrets.")
    sys.exit()
else:
    print("✅ Security credentials loaded successfully.")

# --- BOT CONFIGURATION ---
HISTORY_FILE = 'sent_alerts.json'
MAX_GAMES_TO_SCAN = 50 

BOOKMAKER_LINKS = {
    'draftkings': 'https://sportsbook.draftkings.com',
    'fanduel': 'https://sportsbook.fanduel.com',
    'betmgm': 'https://sports.betmgm.com',
    'espnbet': 'https://espnbet.com',
    'bet365': 'https://www.bet365.com',
    'kalshi': 'https://kalshi.com',
    'novig': 'https://novig.us'
}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)

def iso_to_unix(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except Exception:
        return None

def format_odds(odds):
    """Adds a plus sign to positive odds for cleaner display."""
    return f"+{odds}" if odds > 0 else str(odds)

def american_to_implied(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

def implied_to_american(prob):
    """Converts a true probability back to American odds (Fair Value)."""
    if prob == 0 or prob == 1: return None
    if prob > 0.5:
        return int(-round((prob / (1 - prob)) * 100))
    else:
        return int(round(((1 - prob) / prob) * 100))

def american_to_decimal(odds):
    if odds > 0:
        return (odds / 100) + 1
    else:
        return (100 / abs(odds)) + 1

def calculate_edge_metrics(sharp_odds_target, sharp_odds_opponent, soft_odds_target):
    """Calculates EV, Kelly, and Fair Value Odds."""
    # 1. De-vig the sharp market to get true probabilities
    ip_target = american_to_implied(sharp_odds_target)
    ip_opponent = american_to_implied(sharp_odds_opponent)
    true_prob_win = ip_target / (ip_target + ip_opponent)
    true_prob_lose = 1 -