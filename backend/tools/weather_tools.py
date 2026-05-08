"""Legacy weather tool kept for SuperMew compatibility."""

from __future__ import annotations

from typing import Optional

import requests

from config import WEATHER


def get_current_weather(location: str, extensions: Optional[str] = "base") -> str:
    """Get weather information from AMap."""
    if not location:
        return "location cannot be empty."
    if extensions not in ("base", "all"):
        return "extensions must be 'base' or 'all'."
    if not WEATHER.amap_weather_api or not WEATHER.amap_api_key:
        return "Weather service is not configured; missing AMAP_WEATHER_API or AMAP_API_KEY."

    params = {
        "key": WEATHER.amap_api_key,
        "city": location,
        "extensions": extensions,
        "output": "json",
    }
    try:
        resp = requests.get(WEATHER.amap_weather_api, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            return f"Weather query failed: {data.get('info', 'unknown error')}"

        if extensions == "base":
            lives = data.get("lives", [])
            if not lives:
                return f"No weather data found for {location}."
            w = lives[0]
            return (
                f"{w.get('city', location)} realtime weather\n"
                f"Weather: {w.get('weather', 'unknown')}\n"
                f"Temperature: {w.get('temperature', 'unknown')} C\n"
                f"Humidity: {w.get('humidity', 'unknown')}%\n"
                f"Wind: {w.get('winddirection', 'unknown')} {w.get('windpower', 'unknown')}\n"
                f"Updated: {w.get('reporttime', 'unknown')}"
            )

        forecasts = data.get("forecasts", [])
        if not forecasts:
            return f"No weather forecast found for {location}."
        f0 = forecasts[0]
        casts = f0.get("casts") or []
        today = casts[0] if casts else {}
        return (
            f"{f0.get('city', location)} weather forecast\n"
            f"Updated: {f0.get('reporttime', 'unknown')}\n"
            f"Today: {today.get('dayweather', 'unknown')} / {today.get('nightweather', 'unknown')}\n"
            f"Temperature: {today.get('nighttemp', 'unknown')}~{today.get('daytemp', 'unknown')} C"
        )
    except requests.exceptions.Timeout:
        return "Weather service request timed out."
    except requests.exceptions.RequestException as e:
        return f"Weather service request failed: {e}"
    except Exception as e:
        return f"Weather data parsing failed: {e}"
