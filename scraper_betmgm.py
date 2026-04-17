RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_URLS = [url.strip() for url in RAW_PROXY_LIST.replace("\n", ",").split(",") if url.strip()]


def get_proxy_settings():
    """Get proxy settings in Playwright format or None for direct connection."""
    if not PROXY_URLS:
        return None
    chosen_url = random.choice(PROXY_URLS)
    try:
        return {"server": chosen_url}
    except Exception as exc:
        print(f"Failed to parse proxy URL: {exc}")
        return None


def scrape_betmgm():
    try:
        data = None

        with sync_playwright() as playwright:
            proxy_settings = get_proxy_settings()
            browser = None
            
            if proxy_settings:
                try:
                    print(f"Attempting connection with proxy...")
                    browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
                except Exception as proxy_exc:
                    print(f"Proxy connection failed ({proxy_exc}), falling back to direct connection...")
                    browser = None
            
            if not browser:
                browser = playwright.chromium.launch(headless=True)