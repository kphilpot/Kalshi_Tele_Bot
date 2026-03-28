"""
kalshi.py — Kalshi REST API client.

Handles:
  - RSA-PSS API key authentication (per-request signing, no session tokens)
  - Two-tier weather market discovery (series ticker → broad category fallback)
  - Bracket parsing from market titles
  - Range-based bracket matching (confirmed high falls within bracket range)
"""

import base64
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

from config import (
    KALSHI_MARKETS_URL,
    KALSHI_MARKETS_PATH,
    get_kalshi_api_key_id,
    get_kalshi_private_key_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KalshiClient
# ---------------------------------------------------------------------------

class KalshiClient:
    """
    Manages authenticated requests against the Kalshi trade API using
    RSA-PSS per-request signing.

    Usage pattern (inside a poll cycle):
        markets = await client.fetch_weather_markets(http_client, "Austin")
        match = client.find_bracket_for_temp(markets, 84.0)
    """

    def __init__(self) -> None:
        self._api_key_id = get_kalshi_api_key_id()
        self._private_key = None
        key_path = get_kalshi_private_key_path()
        if key_path:
            try:
                with open(key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(
                        f.read(), password=None
                    )
                logger.info("Kalshi private key loaded from %s", key_path)
            except Exception as exc:
                logger.error("Failed to load Kalshi private key from %s: %s", key_path, exc)

    # ------------------------------------------------------------------
    # Authentication (RSA-PSS per-request signing)
    # ------------------------------------------------------------------

    @property
    def _is_configured(self) -> bool:
        return bool(self._api_key_id and self._private_key)

    def _auth_headers(self, method: str, path: str) -> dict:
        """
        Build Kalshi auth headers for a single request.

        Each request is signed with:
          KALSHI-ACCESS-KEY:       API key ID
          KALSHI-ACCESS-TIMESTAMP: current ms timestamp
          KALSHI-ACCESS-SIGNATURE: RSA-PSS(SHA256) of "{timestamp}{METHOD}{path}"
        """
        ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        message = f"{ts}{method}{path}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    # Keep this for backward compat with scheduler.py calls
    async def ensure_authenticated(
        self, client: httpx.AsyncClient
    ) -> tuple[bool, Optional[str]]:
        """
        No-op for RSA-PSS auth — each request is self-authenticating.
        Returns (success, error_string) for backward compatibility.
        """
        if not self._is_configured:
            return False, (
                "Kalshi API key not configured — set KALSHI_API_KEY_ID and "
                "KALSHI_PRIVATE_KEY_PATH in .env"
            )
        return True, None

    def invalidate_token(self) -> None:
        """No-op — RSA-PSS auth has no session tokens."""
        pass

    # ------------------------------------------------------------------
    # Market discovery
    # ------------------------------------------------------------------

    async def fetch_weather_markets(
        self,
        client: httpx.AsyncClient,
        city_display_name: str,
        series_candidates: tuple[str, ...] = (),
    ) -> tuple[list[dict], Optional[str]]:
        """
        Fetch open high-temperature markets for *city_display_name*.

        Tier 1: Try known series tickers (fast, precise).
        Tier 2: Broad category search, filter client-side.

        Returns (list of market dicts, error_string).
        Market dicts include injected 'parsed_bracket' key if parseable.
        """
        if not self._is_configured:
            return [], "Kalshi API key not configured"

        # Tier 1 — try known series tickers
        # No city filter needed here — series_ticker already scopes to the city
        for series in series_candidates:
            markets, err = await self._fetch_by_series(client, series)
            if err:
                logger.debug("Tier1 series %s error: %s", series, err)
                continue
            markets = self._filter_high_only(markets)
            if markets:
                logger.info(
                    "Kalshi Tier1: found %d markets for %s via series %s",
                    len(markets), city_display_name, series,
                )
                return self._annotate_brackets(markets), None

        # Tier 2 — broad category search
        logger.info(
            "Kalshi Tier1 found nothing for %s — falling back to Tier2 broad search",
            city_display_name,
        )
        markets, err = await self._fetch_broad(client)
        if err:
            return [], err
        markets = self._filter_high_only(markets)
        markets = self._filter_by_city(markets, city_display_name)
        if not markets:
            return [], f"Kalshi Tier2: no high-temp markets found for {city_display_name}"

        logger.info(
            "Kalshi Tier2: found %d markets for %s", len(markets), city_display_name
        )
        return self._annotate_brackets(markets), None

    async def _fetch_by_series(
        self, client: httpx.AsyncClient, series_ticker: str
    ) -> tuple[list[dict], Optional[str]]:
        params = {"status": "open", "series_ticker": series_ticker, "limit": "100"}
        return await self._get_markets(client, params)

    async def _fetch_broad(
        self, client: httpx.AsyncClient
    ) -> tuple[list[dict], Optional[str]]:
        params = {"status": "open", "limit": "200"}
        return await self._get_markets(client, params)

    async def _get_markets(
        self,
        client: httpx.AsyncClient,
        params: dict,
    ) -> tuple[list[dict], Optional[str]]:
        """Raw GET /markets call with RSA-PSS auth."""
        try:
            headers = self._auth_headers("GET", KALSHI_MARKETS_PATH)
            resp = await client.get(
                KALSHI_MARKETS_URL,
                params=params,
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            markets = data.get("markets", [])
            return markets, None
        except httpx.HTTPStatusError as exc:
            return [], f"Kalshi /markets HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:
            return [], f"Kalshi /markets error: {exc}"

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_high_only(markets: list[dict]) -> list[dict]:
        """Remove any market that appears to be a LOW temperature market."""
        filtered = []
        for m in markets:
            title = (m.get("title") or "").lower()
            # Explicit low-temp keywords
            if any(kw in title for kw in ("low temp", "minimum", "low temperature", " low ")):
                continue
            filtered.append(m)
        return filtered

    @staticmethod
    def _filter_by_city(markets: list[dict], city: str) -> list[dict]:
        """Keep markets whose title contains the city name AND the word 'high'."""
        city_lower = city.lower()
        return [
            m for m in markets
            if city_lower in (m.get("title") or "").lower()
            and "high" in (m.get("title") or "").lower()
        ]

    # ------------------------------------------------------------------
    # Bracket parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_bracket_from_title(title: str) -> Optional[tuple[float, float]]:
        """
        Extract (low, high) temperature bracket from a market title string.

        Handles these patterns (in order of specificity):
          "between 83 and 84"         → (83, 84)
          "83 to 84 degrees"          → (83, 84)
          "83° to 84°"               → (83, 84)
          "83-84°F" or "83-84"        → (83, 84)
          "above 90" / "90 or above"  → (90, inf)
          "below 70" / "70 or below"  → (-inf, 70)
        """
        t = title

        # Pattern: "between X and Y" (with optional ° after numbers)
        m = re.search(r"between\s+(\d+(?:\.\d+)?)°?\s+and\s+(\d+(?:\.\d+)?)°?", t, re.IGNORECASE)
        if m:
            return float(m.group(1)), float(m.group(2))

        # Pattern: "X to Y" (with optional ° after numbers)
        m = re.search(r"(\d+(?:\.\d+)?)°?\s+to\s+(\d+(?:\.\d+)?)°?", t, re.IGNORECASE)
        if m:
            return float(m.group(1)), float(m.group(2))

        # Pattern: "X-Y" or "X–Y" (en dash, with optional °)
        m = re.search(r"(\d+(?:\.\d+)?)°?\s*[-\u2013]\s*(\d+(?:\.\d+)?)°?", t)
        if m:
            low, high = float(m.group(1)), float(m.group(2))
            if low < high:   # Sanity check (not a date range etc.)
                return low, high

        # Pattern: "above X" / "over X" / "X or above" (with optional °)
        m = re.search(r"(?:above|over)\s+(\d+(?:\.\d+)?)°?", t, re.IGNORECASE)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)°?\s+or\s+above", t, re.IGNORECASE)
        if m:
            return float(m.group(1)), float("inf")

        # Pattern: "below X" / "under X" / "X or below" (with optional °)
        m = re.search(r"(?:below|under)\s+(\d+(?:\.\d+)?)°?", t, re.IGNORECASE)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)°?\s+or\s+below", t, re.IGNORECASE)
        if m:
            return float("-inf"), float(m.group(1))

        return None

    def _annotate_brackets(self, markets: list[dict]) -> list[dict]:
        """Inject 'parsed_bracket' into each market dict."""
        for m in markets:
            title = m.get("title") or ""
            m["parsed_bracket"] = self.parse_bracket_from_title(title)
        return markets

    # ------------------------------------------------------------------
    # Bracket matching
    # ------------------------------------------------------------------

    def find_bracket_for_temp(
        self,
        markets: list[dict],
        confirmed_high: float,
    ) -> Optional[dict]:
        """
        Find the market whose bracket contains *confirmed_high*.

        E.g. confirmed_high=84 → bracket (83, 84) matches; bracket (85, 86) does NOT.

        Returns the matching market dict (with 'parsed_bracket' key), or None.
        """
        candidates = []
        for m in markets:
            bracket = m.get("parsed_bracket")
            if bracket is None:
                continue
            low, high = bracket
            if low <= confirmed_high <= high:
                candidates.append(m)

        if not candidates:
            logger.info(
                "No bracket found containing %.0f°F among %d markets",
                confirmed_high, len(markets),
            )
            return None

        if len(candidates) == 1:
            return candidates[0]

        # Multiple matches — prefer the one closest to today's close time
        def _close_sort_key(m: dict) -> datetime:
            ct = m.get("close_time") or m.get("expiration_time") or ""
            try:
                return datetime.fromisoformat(ct.replace("Z", "+00:00"))
            except Exception:
                return datetime.max.replace(tzinfo=timezone.utc)

        candidates.sort(key=_close_sort_key)
        return candidates[0]

    # ------------------------------------------------------------------
    # Price extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def extract_yes_ask(market: dict) -> Optional[float]:
        """
        Return YES ask price as a float in dollars (0.00–1.00).

        Kalshi may return price in several fields — check common names.
        """
        for key in ("yes_ask", "yes_ask_dollars", "yes_ask_price"):
            val = market.get(key)
            if val is not None:
                try:
                    price = float(val)
                    # Kalshi sometimes returns cents (0–100); normalise to dollars
                    if price > 1.0:
                        price = price / 100.0
                    return price
                except (ValueError, TypeError):
                    continue

        # Try nested orderbook structure
        ob = market.get("orderbook") or {}
        yes_asks = ob.get("yes", {}).get("ask")
        if yes_asks and isinstance(yes_asks, list) and yes_asks:
            try:
                price = float(yes_asks[0][0])
                if price > 1.0:
                    price = price / 100.0
                return price
            except Exception:
                pass

        return None

    @staticmethod
    def extract_close_time(market: dict) -> Optional[datetime]:
        """Return market close time as a UTC-aware datetime."""
        for key in ("close_time", "expiration_time", "close_date"):
            val = market.get(key)
            if val:
                try:
                    return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                except Exception:
                    continue
        return None
