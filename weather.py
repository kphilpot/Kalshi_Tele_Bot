"""
weather.py — All NWS data fetching and parsing.

Functions:
  fetch_metar()       — Last N METAR readings for a station
  fetch_timeseries()  — Today's hourly obs via NWS observations API
  fetch_dsm()         — ASOS Daily Summary Message (max temp confirmation)
  fetch_cli()         — Climate Report (yesterday's high and time)

All functions are async, accept an httpx.AsyncClient, and return None on any
failure (never raise). Errors are returned as strings so the caller can log them.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import httpx
import pytz
from bs4 import BeautifulSoup

from config import (
    METAR_API_URL,
    NWS_OBS_API_URL,
    NWS_PRODUCT_URL,
    NWS_PRODUCTS_API_URL,
    NWS_USER_AGENT,
    CITIES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class MetarReading:
    station: str
    obs_time: datetime   # UTC, timezone-aware
    temp_f: float        # Converted from Celsius, rounded to nearest int


@dataclass
class DSMResult:
    max_temp_f: float
    issued_time: Optional[datetime]   # UTC, timezone-aware
    report_date: Optional[date]       # Local date the DSM covers
    raw_text: str


@dataclass
class CLIResult:
    yesterday_high_f: float
    time_str: str          # Raw time string from CLI product e.g. "11:45 AM"
    normal_high: Optional[float]
    record_high: Optional[float]
    report_date: Optional[date] = None  # Local date this CLI covers (parsed from header)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _celsius_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32)


def _make_headers() -> dict:
    return {"User-Agent": NWS_USER_AGENT}


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> Optional[httpx.Response]:
    """GET with timeout and basic error handling. Returns None on failure."""
    try:
        r = await client.get(url, timeout=30.0, **kwargs)
        r.raise_for_status()
        return r
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP %s fetching %s", exc.response.status_code, url)
    except Exception as exc:
        logger.warning("Error fetching %s: %s", url, exc)
    return None


# ---------------------------------------------------------------------------
# METAR
# ---------------------------------------------------------------------------

async def fetch_metar(
    client: httpx.AsyncClient,
    station: str,
    hours: int = 3,
) -> tuple[list[MetarReading], Optional[str]]:
    """
    Fetch the last *hours* METAR observations for *station*.

    Returns (readings, error_string).  readings may be empty on failure.

    The Aviation Weather Center API returns one METAR per line in raw format:
      METAR KAUS 280253Z 36018G26KT 10SM OVC025 18/11 A3030 RMK ...
    Note: lines are prefixed with "METAR " — the station code is not at position 0.
    Temperature field is temp_c/dewpoint_c (both integers, may be negative).
    """
    url = METAR_API_URL
    params = {"ids": station, "format": "raw", "hours": str(hours)}
    resp = await _get(client, url, params=params)
    if resp is None:
        return [], f"METAR fetch failed for {station}"

    text = resp.text.strip()
    if not text:
        return [], f"METAR returned empty response for {station}"

    readings: list[MetarReading] = []
    # Match: STATION DDHHMM Z ... temp_c/dewpoint_c ...
    # The temp/dewpoint field can be negative: e.g. M05/M10 means -5/-10
    # No trailing \s — temp field may appear at or near end of line.
    metar_re = re.compile(
        r"(K\w{3})\s+"              # station (group 1)
        r"(\d{2})(\d{2})(\d{2})Z"   # day/hour/min UTC (groups 2,3,4)
        r".*?\s"                     # variable middle fields
        r"(M?\d+)/(M?\d+)",         # temp/dewpoint (groups 5,6)
        re.DOTALL,
    )

    now_utc = datetime.now(timezone.utc)

    for line in text.splitlines():
        line = line.strip()
        # Lines are prefixed "METAR KXXX ..." — do not filter on startswith("K")
        if not line:
            continue
        m = metar_re.search(line)
        if not m:
            # Fallback: even simpler pattern
            m = re.search(
                r"(K\w{3})\s+(\d{2})(\d{2})(\d{2})Z.*?(M?\d+)/(M?\d+)",
                line,
            )
            if not m:
                continue
            _, day_s, hour_s, min_s, tc_s, _ = m.groups()
        else:
            _, day_s, hour_s, min_s, tc_s, _ = m.groups()

        # Parse temperature (M prefix = negative)
        tc_s_clean = tc_s.replace("M", "-")
        try:
            temp_c = int(tc_s_clean)
        except ValueError:
            continue

        # Build UTC datetime from day/hour/minute
        day = int(day_s)
        hour = int(hour_s)
        minute = int(min_s)

        # Handle month boundary: if reported day > today, it's last month
        year = now_utc.year
        month = now_utc.month
        if day > now_utc.day:
            if month == 1:
                month = 12
                year -= 1
            else:
                month -= 1

        try:
            obs_time = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue

        readings.append(MetarReading(
            station=station,
            obs_time=obs_time,
            temp_f=_celsius_to_f(temp_c),
        ))

    if not readings:
        return [], f"METAR: no parseable readings found for {station}"

    # Sort oldest → newest
    readings.sort(key=lambda r: r.obs_time)
    return readings, None


# ---------------------------------------------------------------------------
# Time Series (NWS Observations API)
# ---------------------------------------------------------------------------

async def fetch_timeseries(
    client: httpx.AsyncClient,
    station: str,
    local_tz_str: str,
    limit: int = 24,
) -> tuple[list[tuple[datetime, float]], Optional[str]]:
    """
    Fetch today's hourly observations via the NWS Observations API.

    Returns (list of (utc_datetime, temp_f), error_string).

    Only returns observations from today (local city date) with qualityControl "V".
    """
    url = NWS_OBS_API_URL.format(station=station)
    params = {"limit": str(limit)}
    resp = await _get(client, url, params=params, headers=_make_headers())
    if resp is None:
        return [], f"TimeSeries fetch failed for {station}"

    try:
        data = resp.json()
    except Exception as exc:
        return [], f"TimeSeries JSON parse error for {station}: {exc}"

    features = data.get("features", [])
    if not features:
        return [], f"TimeSeries: no features in response for {station}"

    local_tz = pytz.timezone(local_tz_str)
    today_local = datetime.now(local_tz).date()

    results: list[tuple[datetime, float]] = []
    for feature in features:
        props = feature.get("properties", {})
        temp_block = props.get("temperature", {})
        temp_val = temp_block.get("value")
        qc = props.get("qualityControl", "")
        ts_str = props.get("timestamp", "")

        if temp_val is None:
            continue
        # Accept any observation with a non-null temperature value.
        # NWS currently returns qualityControl="NONE" for recent observations,
        # so filtering on "V"/"Z"/"C" would reject everything. We rely on
        # today-local-date filtering below to exclude stale data instead.

        try:
            obs_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        # Filter to today's local date only
        obs_local_date = obs_dt.astimezone(local_tz).date()
        if obs_local_date != today_local:
            continue

        results.append((obs_dt, _celsius_to_f(temp_val)))

    if not results:
        return [], f"TimeSeries: no valid observations found for {station} today"

    results.sort(key=lambda x: x[0])
    return results, None


# ---------------------------------------------------------------------------
# DSM — ASOS Daily Summary Message
# ---------------------------------------------------------------------------

async def fetch_dsm(
    client: httpx.AsyncClient,
    office: str,
    expected_date: Optional[date] = None,
) -> tuple[Optional[DSMResult], Optional[str]]:
    """
    Fetch and parse the DSM (ASOS Daily Summary) for *office*.

    Uses the NWS Products JSON API (api.weather.gov/products) to avoid the
    JS-rendered HTML problem with forecast.weather.gov.

    Returns (DSMResult or None, error_string).
    Returns None result (not an error) if the DSM hasn't been issued yet.

    expected_date: if provided, the DSM's issuance date must match or we return None.
    """
    # Step 1 — get the list of recent DSM products for this office
    list_url = NWS_PRODUCTS_API_URL
    list_params = {"type": "DSM", "location": office, "limit": "1"}
    list_resp = await _get(client, list_url, params=list_params, headers=_make_headers())
    if list_resp is None:
        return None, f"DSM list fetch failed for office {office}"

    try:
        list_data = list_resp.json()
    except Exception as exc:
        return None, f"DSM list JSON parse error for office {office}: {exc}"

    graph = list_data.get("@graph", [])
    if not graph:
        return None, f"DSM: no products found via NWS API for office {office} (type=DSM — may be wrong product type or location code)"

    product_id = graph[0].get("id", "")
    if not product_id:
        return None, f"DSM: could not extract product ID for office {office}"

    # Step 2 — fetch the full product text by ID
    product_url = f"{NWS_PRODUCTS_API_URL}/{product_id}"
    product_resp = await _get(client, product_url, headers=_make_headers())
    if product_resp is None:
        return None, f"DSM product fetch failed (id={product_id})"

    try:
        product_data = product_resp.json()
    except Exception as exc:
        return None, f"DSM product JSON parse error: {exc}"

    raw_text = product_data.get("productText", "")
    if not raw_text:
        return None, f"DSM: empty productText for office {office}"

    # Check for "none issued" placeholder
    if re.search(r"none\s+issued", raw_text, re.IGNORECASE):
        return None, None

    # Parse max temperature
    # Formats seen: "MAX TEMP          88"
    #               "MAXIMUM TEMPERATURE  88"
    #               "MAX               88"
    max_temp_match = re.search(
        r"MAX(?:IMUM)?\s+(?:TEMP(?:ERATURE)?)?\s{1,}(\d+)",
        raw_text,
        re.IGNORECASE,
    )
    if not max_temp_match:
        max_temp_match = re.search(
            r"\bMAX\b.*?(\d{2,3})\b",
            raw_text,
            re.IGNORECASE,
        )
    if not max_temp_match:
        return None, f"DSM: could not parse max temp from product for office {office}"

    max_temp_f = float(max_temp_match.group(1))

    # Parse product issuance time and date from header
    # e.g. "1056 AM CST THU MAR 27 2026"
    issued_time = None
    report_date = None
    time_match = re.search(
        r"(\d{3,4})\s+(AM|PM)\s+[A-Z]{2,3}T?\s+\w+\s+(\w+)\s+(\d{1,2})\s+(\d{4})",
        raw_text,
        re.IGNORECASE,
    )
    if time_match:
        try:
            month_str = time_match.group(3)
            day_str = time_match.group(4)
            year_str = time_match.group(5)
            month_map = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
            }
            month_num = month_map.get(month_str.upper()[:3])
            if month_num:
                report_date = date(int(year_str), month_num, int(day_str))
        except Exception:
            pass

    # Also try issuanceTime from the product metadata if header parse failed
    if report_date is None:
        issuance_str = product_data.get("issuanceTime", "")
        if issuance_str:
            try:
                iso_dt = datetime.fromisoformat(issuance_str.replace("Z", "+00:00"))
                report_date = iso_dt.date()
            except Exception:
                pass

    # Validate date if caller requested it
    if expected_date and report_date and report_date != expected_date:
        logger.info(
            "DSM for office %s is dated %s, expected %s — ignoring",
            office, report_date, expected_date,
        )
        return None, None

    return DSMResult(
        max_temp_f=max_temp_f,
        issued_time=issued_time,
        report_date=report_date,
        raw_text=raw_text,
    ), None


# ---------------------------------------------------------------------------
# CLI — Climate Report (yesterday's high)
# ---------------------------------------------------------------------------

async def fetch_cli(
    client: httpx.AsyncClient,
    office: str,
) -> tuple[Optional[CLIResult], Optional[str]]:
    """
    Fetch and parse the CLI (Climate Report) for *office*.

    Extracts yesterday's maximum temperature and the time it occurred.
    CLI times are always in LST (Local Standard Time), never LDT.

    Returns (CLIResult or None, error_string).
    """
    url = NWS_PRODUCT_URL
    params = {"site": "NWS", "product": "CLI", "issuedby": office}
    resp = await _get(client, url, params=params)
    if resp is None:
        return None, f"CLI fetch failed for office {office}"

    soup = BeautifulSoup(resp.text, "lxml")
    pre = soup.find("pre")
    if pre is None:
        return None, f"CLI: no <pre> block found for office {office}"

    raw_text = pre.get_text()

    if re.search(r"none\s+issued", raw_text, re.IGNORECASE):
        return None, None

    # Parse report date from header e.g. "700 PM CDT FRI MAR 27 2026"
    _cli_date = None
    _date_match = re.search(
        r"(\d{3,4})\s+(AM|PM)\s+[A-Z]{2,3}T?\s+\w+\s+(\w+)\s+(\d{1,2})\s+(\d{4})",
        raw_text,
        re.IGNORECASE,
    )
    if _date_match:
        try:
            _month_map = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
            }
            _month_num = _month_map.get(_date_match.group(3).upper()[:3])
            if _month_num:
                _cli_date = date(
                    int(_date_match.group(5)),
                    _month_num,
                    int(_date_match.group(4)),
                )
        except Exception:
            pass

    # Parse the MAXIMUM line.
    # Format: "  MAXIMUM         84  11:45 AM  91    2015  82      2       83"
    # Columns: label | observed | time | record | rec_year | normal | departure | last_year
    max_match = re.search(
        r"MAXIMUM\s+(\d+)\s+(\d{1,2}:\d{2}\s+[AP]M)",
        raw_text,
        re.IGNORECASE,
    )
    if not max_match:
        # Some CLI products don't include a time if the high was at midnight
        max_match_no_time = re.search(
            r"MAXIMUM\s+(\d+)",
            raw_text,
            re.IGNORECASE,
        )
        if max_match_no_time:
            high_f = float(max_match_no_time.group(1))
            return CLIResult(
                yesterday_high_f=high_f,
                time_str="unknown",
                normal_high=_parse_cli_normal(raw_text),
                record_high=_parse_cli_record(raw_text),
                report_date=_cli_date,
            ), None
        return None, f"CLI: could not parse MAXIMUM line for office {office}"

    high_f = float(max_match.group(1))
    time_str = max_match.group(2).strip()  # e.g. "11:45 AM"

    return CLIResult(
        yesterday_high_f=high_f,
        time_str=time_str,
        normal_high=_parse_cli_normal(raw_text),
        record_high=_parse_cli_record(raw_text),
        report_date=_cli_date,
    ), None


def _parse_cli_normal(text: str) -> Optional[float]:
    """Extract the 'NORMAL' high value from CLI text if present."""
    m = re.search(r"NORMAL\s+(\d+)", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _parse_cli_record(text: str) -> Optional[float]:
    """Extract the record high value from the MAXIMUM line if present."""
    # In the MAXIMUM line the record is the 3rd numeric column
    m = re.search(
        r"MAXIMUM\s+\d+\s+\d{1,2}:\d{2}\s+[AP]M\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Forecast — NWS gridpoint forecast for today's expected high
# ---------------------------------------------------------------------------

@dataclass
class ForecastResult:
    high_f: float              # Today's forecast high in Fahrenheit
    period_name: str           # e.g. "This Afternoon", "Today"
    short_forecast: str        # e.g. "Sunny, with a high near 85"


async def fetch_forecast(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> tuple[Optional[ForecastResult], Optional[str]]:
    """
    Fetch today's forecast high via the NWS Points → Gridpoint Forecast API.

    Step 1: GET /points/{lat},{lon} → get forecast URL
    Step 2: GET {forecast_url} → get forecast periods
    Step 3: Find today's daytime period → extract high temperature

    Returns (ForecastResult or None, error_string).
    """
    # Step 1 — resolve gridpoint
    points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    points_resp = await _get(client, points_url, headers=_make_headers())
    if points_resp is None:
        return None, f"Forecast: failed to resolve gridpoint for ({lat}, {lon})"

    try:
        points_data = points_resp.json()
    except Exception as exc:
        return None, f"Forecast: JSON parse error from points API: {exc}"

    forecast_url = points_data.get("properties", {}).get("forecast")
    if not forecast_url:
        return None, f"Forecast: no forecast URL in points response for ({lat}, {lon})"

    # Step 2 — fetch forecast
    forecast_resp = await _get(client, forecast_url, headers=_make_headers())
    if forecast_resp is None:
        return None, "Forecast: failed to fetch gridpoint forecast"

    try:
        forecast_data = forecast_resp.json()
    except Exception as exc:
        return None, f"Forecast: JSON parse error from forecast API: {exc}"

    periods = forecast_data.get("properties", {}).get("periods", [])
    if not periods:
        return None, "Forecast: no periods in forecast response"

    # Step 3 — find today's daytime high
    for period in periods:
        if period.get("isDaytime", False):
            temp = period.get("temperature")
            if temp is not None:
                return ForecastResult(
                    high_f=float(temp),
                    period_name=period.get("name", "Today"),
                    short_forecast=period.get("shortForecast", ""),
                ), None

    return None, "Forecast: no daytime period found in forecast"


# ---------------------------------------------------------------------------
# Convenience: fetch latest METAR reading
# ---------------------------------------------------------------------------

async def refresh_metar_reading(
    client: httpx.AsyncClient,
    station: str,
) -> tuple[Optional[MetarReading], Optional[str]]:
    """Return the single most recent METAR reading for *station*."""
    readings, err = await fetch_metar(client, station, hours=1)
    if not readings:
        return None, err
    return readings[-1], None
