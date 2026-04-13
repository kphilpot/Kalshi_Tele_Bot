#!/usr/bin/env py
"""
Diagnostic script to test Kalshi API connection and bracket availability.
Tests whether Kalshi has appropriate brackets for high temperatures.
"""

import asyncio
import json
import logging
from datetime import date, datetime

import httpx

from config import CITIES
from kalshi import KalshiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def test_kalshi_brackets():
    """Test Kalshi API connection and bracket availability for all cities."""
    client = KalshiClient()

    # Check if client is configured
    is_configured, auth_err = await client.ensure_authenticated(httpx.AsyncClient())
    if not is_configured:
        logger.error("❌ Kalshi not configured: %s", auth_err)
        return

    logger.info("✅ Kalshi API configured")

    async with httpx.AsyncClient() as http_client:
        for station, config in CITIES.items():
            logger.info("\n" + "="*60)
            logger.info("Testing %s (%s)", config.display_name, station)
            logger.info("="*60)

            # Fetch markets
            markets, err = await client.fetch_weather_markets(
                http_client,
                config.display_name,
                series_candidates=config.kalshi_series_candidates,
                target_date=date.today(),
            )

            if err:
                logger.error("❌ Fetch failed: %s", err)
                continue

            if not markets:
                logger.warning("⚠️  No markets found")
                continue

            logger.info("✅ Found %d markets", len(markets))

            # Extract and display all available brackets
            brackets = {}
            for m in markets:
                bracket = m.get("parsed_bracket")
                if bracket:
                    low, high = bracket
                    if low == float("-inf"):
                        bracket_str = f"below {high:.0f}"
                    elif high == float("inf"):
                        bracket_str = f"above {low:.0f}"
                    else:
                        bracket_str = f"{low:.0f}-{high:.0f}"

                    ticker = m.get("ticker") or m.get("id") or "unknown"
                    brackets[bracket_str] = {
                        "ticker": ticker,
                        "low": low,
                        "high": high,
                    }

            logger.info("Available brackets:")
            for bracket_str, info in sorted(brackets.items()):
                logger.info("  • %s (ticker: %s)", bracket_str, info["ticker"])

            # Test specific temperatures
            test_temps = [80.0, 81.0, 82.0, 83.0, 84.0, 85.0]
            logger.info("\nBracket matching for test temperatures:")
            for temp in test_temps:
                match, reason = client.find_bracket_for_temp(markets, temp)
                if match:
                    bracket = match.get("parsed_bracket")
                    ticker = match.get("ticker") or match.get("id") or "unknown"
                    if bracket:
                        bracket_str = f"{bracket[0]:.0f}-{bracket[1]:.0f}"
                        logger.info("  %.0f°F → ✅ %s (ticker: %s)", temp, bracket_str, ticker)
                    else:
                        logger.info("  %.0f°F → ✅ (parsed, no bracket data)", temp)
                else:
                    logger.warning("  %.0f°F → ❌ %s", temp, reason)

            # Check for recent problematic temperatures
            logger.info("\n" + "-"*60)


if __name__ == "__main__":
    asyncio.run(test_kalshi_brackets())
