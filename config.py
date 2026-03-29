"""
config.py — City configurations, URL constants, and environment loading.
"""

import os
from dataclasses import dataclass
from datetime import time

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# City configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CityConfig:
    station: str          # ICAO station ID (e.g. KAUS)
    office: str           # NWS office code for products (e.g. AUS)
    tz: str               # pytz timezone string
    display_name: str     # Human-readable city name
    lat: float            # Station latitude (for NWS forecast API)
    lon: float            # Station longitude (for NWS forecast API)
    dsm_timeout_local: time   # Local time after which CLI confirmation is late
    # Known Kalshi series ticker candidates (tried in order, Tier 1 discovery)
    kalshi_series_candidates: tuple[str, ...]
    # T-Group settlement formula city bias (compensates for sensor-sampling gaps)
    tgroup_bias: float = 0.0
    # Maximum YES ask price (cents) above which a trade is flagged as not advisable.
    # Set conservatively per city: Miami (most reliable) allows up to 88¢,
    # Chicago 85¢, Austin (most volatile) 80¢.  Ensures a floor on potential profit.
    max_entry_price_cents: int = 90
    # Lock 2 cross-check tolerance (°F).  Austin uses 2.0 — tighter than Miami/Chicago —
    # to catch METAR/NWS Obs disagreements that slip through at 3.0°F on a volatile sensor.
    lock2_tolerance_f: float = 3.0


CITIES: dict[str, CityConfig] = {
    "KAUS": CityConfig(
        station="KAUS",
        office="AUS",
        tz="America/Chicago",
        display_name="Austin",
        lat=30.1945,
        lon=-97.6699,
        dsm_timeout_local=time(20, 0),
        kalshi_series_candidates=("KXHIGHAUS", "KXHIGHAUSTIN", "HIGHAUS"),
        tgroup_bias=0.15,
        max_entry_price_cents=80,
        lock2_tolerance_f=2.0,
    ),
    "KMIA": CityConfig(
        station="KMIA",
        office="MIA",
        tz="America/New_York",
        display_name="Miami",
        lat=25.7959,
        lon=-80.2870,
        dsm_timeout_local=time(20, 0),
        kalshi_series_candidates=("KXHIGHMIA", "KXHIGHMIAMI", "HIGHMIA"),
        max_entry_price_cents=88,
    ),
    "KMDW": CityConfig(
        station="KMDW",
        office="MDW",
        tz="America/Chicago",
        display_name="Chicago",
        lat=41.7868,
        lon=-87.7522,
        dsm_timeout_local=time(20, 0),
        kalshi_series_candidates=("KXHIGHCHI", "KXHIGHCHICAGO", "HIGHCHI"),
        max_entry_price_cents=85,
    ),
}

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

# Aviation Weather Center METAR API (plain text, no auth required)
METAR_API_URL = "https://aviationweather.gov/api/data/metar"

# NWS Observations API (GeoJSON, no auth required)
NWS_OBS_API_URL = "https://api.weather.gov/stations/{station}/observations"

# NWS text products (HTML page with <pre> block) — used for CLI
NWS_PRODUCT_URL = "https://forecast.weather.gov/product.php"

# NWS Products JSON API — used for DSM (avoids JS-rendered HTML)
NWS_PRODUCTS_API_URL = "https://api.weather.gov/products"

# Kalshi REST API
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_MARKETS_URL = f"{KALSHI_BASE_URL}/markets"
KALSHI_MARKETS_PATH = "/trade-api/v2/markets"  # Path component for request signing

# ---------------------------------------------------------------------------
# Bot behaviour constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_MINUTES = 10
PRICE_FLAG_THRESHOLD = 0.96       # 96 cents — above this, flag the trade
POLL_START_HOUR_LOCAL = 12        # Noon local city time
POLL_END_HOUR_EST = 22            # 10 PM EST

# Telegram retry backoff delays in seconds (exponential)
TELEGRAM_RETRY_DELAYS = [30, 60, 120, 240, 300]

# Error log prune age in minutes
ERROR_LOG_PRUNE_MINUTES = 30

# Kalshi API key auth (RSA-PSS signing — no session tokens)

# NWS API User-Agent (required by api.weather.gov policy)
NWS_USER_AGENT = "KalshiWeatherBot/1.0 (weather-monitor-bot)"

# ---------------------------------------------------------------------------
# Environment variable accessors
# ---------------------------------------------------------------------------

def get_telegram_token() -> str:
    val = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not val:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    return val


def get_telegram_chat_id() -> str:
    val = os.getenv("TELEGRAM_CHAT_ID", "")
    if not val:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")
    return val


def get_kalshi_api_key_id() -> str:
    return os.getenv("KALSHI_API_KEY_ID", "")


def get_kalshi_private_key_path() -> str:
    return os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
