"""Discord bot entrypoint that registers and serves the /ev slash command."""

import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils.ev_command import compute_ev_response, format_american

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
_GUILD_ID_RAW = os.getenv("DISCORD_GUILD_ID", "").strip()
GUILD_ID = None
if _GUILD_ID_RAW:
    try:
        GUILD_ID = int(_GUILD_ID_RAW)
    except ValueError:
        print(f"[bot] Invalid DISCORD_GUILD_ID: {_GUILD_ID_RAW}")


class BeeBakedBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        guild = None
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            scope = f"guild {GUILD_ID}" if guild else "global"
        except discord.errors.Forbidden as exc:
            print(f"[bot] Guild sync forbidden for guild {GUILD_ID}: {exc}")
            app_id = self.application_id or getattr(self.user, "id", None)
            if app_id:
                print(
                    f"[bot] Re-authorize with applications.commands scope: "
                    f"https://discord.com/oauth2/authorize?client_id={app_id}"
                    f"&permissions=18432&scope=bot%20applications.commands"
                )
            print("[bot] Falling back to global command sync (may take up to 1 hour).")
            synced = await self.tree.sync()
            scope = "global"
        print(f"Synced {len(synced)} slash command(s) {scope}")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")


bot = BeeBakedBot()


@bot.tree.command(
    name="ev",
    description="Calculate expected value against the Pinnacle fair price.",
)
@app_commands.describe(
    my_odds="Your offered American odds for the side you are betting (e.g. +150 or -110).",
    pinnacle_odds_1="Pinnacle American odds for the SAME side as your bet.",
    pinnacle_odds_2="Pinnacle American odds for the opposite side.",
)
async def ev_command(
    interaction: discord.Interaction,
    my_odds: int,
    pinnacle_odds_1: int,
    pinnacle_odds_2: int,
):
    try:
        result = compute_ev_response(my_odds, pinnacle_odds_1, pinnacle_odds_2)
    except ValueError as exc:
        await interaction.response.send_message(f"Invalid input: {exc}", ephemeral=True)
        return

    embed = discord.Embed(
        title="+EV Calculator",
        color=result["color"],
        description=f"Comparing **{format_american(my_odds)}** to the Pinnacle fair price.",
    )
    embed.add_field(name="Fair Market Price", value=result["fair_american"], inline=False)

    ev_sign = "+" if result["ev_pct"] >= 0 else ""
    embed.add_field(name="EV", value=f"{ev_sign}{result['ev_pct'] * 100:.2f}%", inline=True)
    embed.add_field(name="Recommendation", value=result["recommendation"], inline=True)
    embed.add_field(
        name="True Probability",
        value=f"{result['fair_probability'] * 100:.2f}%",
        inline=True,
    )
    embed.add_field(
        name="Suggested Units",
        value=f"{result['units']:.2f}% of bankroll",
        inline=True,
    )
    embed.set_footer(text="BEE BAKED BETS | The Hive +EV Scanner")
    embed.timestamp = datetime.now(timezone.utc)

    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is required")
    bot.run(DISCORD_BOT_TOKEN)
