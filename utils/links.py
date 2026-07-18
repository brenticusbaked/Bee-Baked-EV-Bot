import urllib.parse


def sportsbook_search_link(bookmaker: str, query_text: str) -> str:
    query = urllib.parse.quote(str(query_text))
    book = str(bookmaker).lower().replace(" ", "").replace("sportsbook", "")
    links = {
        "draftkings": f"https://sportsbook.draftkings.com/search?q={query}",
        "fanduel": f"https://sportsbook.fanduel.com/navigation/search?q={query}",
        "betmgm": f"https://sports.betmgm.com/en/sports/search?q={query}",
        "bet365": f"https://www.bet365.com/#/search?q={query}",
        "caesars": f"https://sportsbook.caesars.com/us/ky/bet/search?q={query}",
        "betrivers": f"https://betrivers.com/?page=search&q={query}",
        "bovada": f"https://www.bovada.lv/sports?search={query}",
        "espn": f"https://espnbet.com/search?q={query}",
        "espnbet": f"https://espnbet.com/search?q={query}",
        "fanatics": f"https://sportsbook.fanatics.com/search?q={query}",
        "prizepicks": f"https://app.prizepicks.com/search/{query}",
        "novig": f"https://novig.us/search?q={query}",
        "prophetx": f"https://app.prophetx.co/search?q={query}",
        "kalshi": f"https://kalshi.com/search?q={query}",
        "polymarket": f"https://polymarket.com/search?q={query}",
        "fliff": f"https://www.getfliff.com/?q={query}",
    }
    return links.get(book, f"https://www.google.com/search?q={urllib.parse.quote(bookmaker)}+{query}")
