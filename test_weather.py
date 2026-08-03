import requests


def test_open_meteo_api():
    """Fetches weather data from the Open-Meteo API for Chicago."""
    url = "https://api.open-meteo.com/v1/forecast"
    # Using Chicago as an example location
    params = {
        "latitude": 41.85,
        "longitude": -87.65,
        "current": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation",
        "hourly": "temperature_2m,wind_speed_10m",
        "timezone": "America/Chicago"
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        print("API Response Structure:")
        print(f"Latitude: {data['latitude']}, Longitude: {data['longitude']}")

        current = data.get("current", {})
        print("\nCurrent Conditions:")
        print(f"Time: {current.get('time')}")
        print(f"Temperature: {current.get('temperature_2m')} {data['current_units'].get('temperature_2m')}")
        print(f"Wind Speed: {current.get('wind_speed_10m')} {data['current_units'].get('wind_speed_10m')}")
        print(f"Precipitation: {current.get('precipitation')} {data['current_units'].get('precipitation')}")

    except requests.exceptions.RequestException as e:
         print(f"Error connecting to Open-Meteo API: {e}")


if __name__ == "__main__":
    test_open_meteo_api()
