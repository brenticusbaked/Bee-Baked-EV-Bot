import argparse
import json

from services.live_edges import find_live_edge_alerts


def _event_with_prices(event_id: str, sharp_a: float, sharp_b: float, soft_a: float) -> dict:
    return {
        "id": event_id,
        "home_team": "Miami Heat",
        "away_team": "Chicago Bulls",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Miami Heat", "price": sharp_a},
                            {"name": "Chicago Bulls", "price": sharp_b},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [{"name": "Miami Heat", "price": soft_a}],
                    }
                ],
            },
        ],
    }


def sample_replay() -> tuple[dict, dict, dict]:
    previous = {"basketball_nba": [_event_with_prices("evt_1", 2.00, 2.00, 2.05)]}
    current = {"basketball_nba": [_event_with_prices("evt_1", 1.80, 2.20, 2.05)]}
    updated = {"basketball_nba": [_event_with_prices("evt_1", 1.80, 2.20, 2.05)]}
    return previous, current, updated


def load_replay(path: str) -> tuple[dict, dict, dict]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("previous", {}), payload.get("current", {}), payload.get("updated", payload.get("current", {}))


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay odds cache snapshots through the live edge classifier.")
    parser.add_argument("--file", help="JSON file with previous/current/updated cache snapshots.")
    parser.add_argument("--json", action="store_true", help="Print raw alert JSON instead of readable summaries.")
    args = parser.parse_args()

    previous, current, updated = load_replay(args.file) if args.file else sample_replay()
    alerts = find_live_edge_alerts(previous, current, updated)

    if args.json:
        print(json.dumps(alerts, indent=2))
        return 0

    if not alerts:
        print("No LIVE HAMMER or WATCHLIST alerts found.")
        return 0

    for index, alert in enumerate(alerts, start=1):
        description = alert["payload"]["embeds"][0]["description"]
        print(f"--- Alert {index}: {alert['lane'].upper()} ---")
        print(description)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
