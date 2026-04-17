import os
import requests

PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")

# Clean up the IP list exactly like the scrapers do
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

def test_connection():
    if not PROXY_IPS or not PROXY_USERNAME or not PROXY_PASSWORD:
        print("❌ Missing proxy secrets in environment variables!")
        return

    chosen_ip = PROXY_IPS[0]
    
    # Check for formatting errors in the GitHub secret
    print(f"🔍 Raw PROXY_LIST secret: '{RAW_PROXY_LIST}'")
    if "http" in chosen_ip:
        print("⚠️ WARNING: Your PROXY_LIST secret contains 'http://' or 'https://'. Remove it! It should just be the domain/IP and port (e.g., us.proxy.com:10000)")
    
    # Format the proxy URL
    proxy_url = f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{chosen_ip}"
    proxies = {"http": proxy_url, "https": proxy_url}
    
    masked_url = f"http://{PROXY_USERNAME}:***@{chosen_ip}"
    print(f"🔌 Attempting connection using: {masked_url}")

    try:
        # Try to reach a basic site through the proxy
        response = requests.get("https://sportsbook.fanduel.com", proxies=proxies, timeout=10)
        print(f"✅ Success! Connected to FanDuel. Status Code: {response.status_code}")
        
    except requests.exceptions.ProxyError as e:
        print(f"❌ PROXY REJECTED THE CONNECTION!")
        print(f"Exact Error: {e}")
        print("-> This usually means wrong username/password format, or your Sticky Session format is invalid.")
    except Exception as e:
        print(f"❌ CONNECTION FAILED!")
        print(f"Exact Error: {e}")

if __name__ == "__main__":
    test_connection()
