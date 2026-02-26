import asyncio
import aiohttp
import time

# ⚠️ PASTE YOUR WEBHOOK URL INSIDE THE QUOTES BELOW ⚠️
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1476284215610839241/2wXJwGwBiFEeCX8KLXDqjzB7bv36upVElcsHFcvhZWomf1Q1ejOh7d4ddZRreSriOIMG"

async def send_test_alert():
    if DISCORD_WEBHOOK_URL == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        print("❌ Please paste your actual webhook URL into the script!")
        return

    print("Sending test alert to Discord...")

    # Creates a fake tip-off time 1 hour from right now
    unix_time = int(time.time()) + 3600 
    
    embed = {
        "title": "🚨 TEST: High-Value +EV Target Acquired 🚨",
        "color": 16766720,
        "image": {"url": "https://pbs.twimg.com/profile_banners/1966599130844733440/1770881333/1080x360"},
        "fields": [
            {"name": "Matchup", "value": "Duke vs North Carolina", "inline": False},
            {"name": "Tip-Off", "value": f"<t:{unix_time}:F>", "inline": False},
            {"name": "Market", "value": "Player Points", "inline": True},
            {"name": "Bet", "value": "Kyle Filipowski Over 15.5", "inline": True},
            {"name": "Odds", "value": "+115", "inline": True},
            {"name": "Fair Value", "value": "-105", "inline": True},
            {"name": "Book", "value": "DraftKings", "inline": True},
            {"name": "Estimated Edge", "value": "4.2%", "inline": True},
            {"name": "Q-Kelly Stakes", "value": "**$1 Unit:** $1.25\n**$10 Unit:** $12.50\n**$100 Unit:** $125.00", "inline": True},
            {"name": "Action", "value": "📱 [Place Bet Here](https://sportsbook.draftkings.com)", "inline": False}
        ],
        "footer": {"text": "Honey Bee Money Premium Alerts"}
    }
    
    payload = {"username": "beebaked EV Bot", "embeds": [embed]}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(DISCORD_WEBHOOK_URL, json=payload) as response:
                if response.status in [200, 204]:
                    print("✅ Test alert sent successfully! Go check your Discord channel.")
                else:
                    print(f"❌ Failed to send alert: HTTP {response.status}")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(send_test_alert())