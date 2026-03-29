"""
backtest/backtest_logger.py — End-of-day snapshot recorder.

Called once per city at 10 PM EST (from eod_job) before state is reset.
Writes a structured JSON record to backtest/data/YYYY-MM-DD_STATION.json.

Each record contains everything needed to:
  - Verify bracket prediction accuracy against CLI ground truth
  - Measure entry timing and price economics (price_history)
  - Audit each stage of the Triple-Lock + Settlement Auditor pipeline
  - Identify which days were tradeable under the per-city price ceiling

The logger is always fail-open — any error is logged and swallowed.
It never blocks the EOD summary message.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from config import CityConfig
from state import DailyState

logger = logging.getLogger(__name__)

BACKTEST_DIR = Path("backtest") / "data"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_day(station: str, state: DailyState, config: CityConfig) -> None:
    """
    Write today's backtest record for *station*.
    Silently absorbs all exceptions so a logging failure never interrupts the bot.
    """
    try:
        _write_record(station, state, config)
    except Exception as exc:
        logger.error("Backtest logger failed for %s: %s", station, exc)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _write_record(station: str, state: DailyState, config: CityConfig) -> None:
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)

    # ── Price snapshots from price_history ──────────────────────────────────
    # price_history entries: [utc_isostr, price, event_label]
    price_at_settlement_audit: float | None = None
    price_at_confirmation: float | None = None

    prices_clean = []
    for entry in state.price_history:
        if len(entry) != 3:
            continue
        utc_str, price, event = entry
        prices_clean.append({"utc": utc_str, "event": event, "yes_ask": price})
        if event == "settlement_audit" and price_at_settlement_audit is None:
            price_at_settlement_audit = price
        elif event == "confirmation" and price_at_confirmation is None:
            price_at_confirmation = price

    # Earliest known price = best proxy for "price at entry opportunity"
    entry_price = price_at_settlement_audit or price_at_confirmation

    # ── Bracket correctness (requires CLI ground truth) ──────────────────────
    bracket_correct: bool | None = None
    settlement_prediction_correct: bool | None = None

    if state.dsm_confirmed and state.dsm_max_temp is not None:
        lo = state.kalshi_bracket_low
        hi = state.kalshi_bracket_high
        actual = state.dsm_max_temp
        if lo is not None and hi is not None:
            if hi == float("inf"):
                bracket_correct = actual >= lo
            elif lo == float("-inf"):
                bracket_correct = actual <= hi
            else:
                bracket_correct = lo <= actual <= hi

        if state.predicted_settlement_f is not None:
            settlement_prediction_correct = (
                abs(state.predicted_settlement_f - state.dsm_max_temp) < 0.5
            )

    # ── Lock inferences ──────────────────────────────────────────────────────
    lock1_inferred_pass: bool | None = None
    if state.morning_model_high is not None and state.suspected_high is not None:
        lower = state.morning_model_high - 5
        upper = state.morning_model_high + 2
        lock1_inferred_pass = lower <= state.suspected_high <= upper

    lock2_diff: float | None = None
    lock2_inferred_pass: bool | None = None
    if state.suspected_high is not None and state.wethr_high is not None:
        lock2_diff = round(abs(state.suspected_high - state.wethr_high), 1)
        lock2_inferred_pass = lock2_diff <= config.lock2_tolerance_f

    # ── Economics ────────────────────────────────────────────────────────────
    potential_profit_cents: int | None = None
    tradeable: bool | None = None
    if entry_price is not None:
        potential_profit_cents = round((1.00 - entry_price) * 100)
        tradeable = entry_price <= (config.max_entry_price_cents / 100)

    # ── Serialise bracket_high (inf is not valid JSON) ───────────────────────
    bracket_high_serialised = (
        None
        if state.kalshi_bracket_high in (float("inf"), None)
        else state.kalshi_bracket_high
    )

    record = {
        "meta": {
            "station": station,
            "city": config.display_name,
            "date": state.date.isoformat(),
            "recorded_at": datetime.utcnow().isoformat() + "Z",
        },
        "setup": {
            "morning_model_high_f": state.morning_model_high,
            "tgroup_bias": config.tgroup_bias,
            "lock2_tolerance_f": config.lock2_tolerance_f,
            "max_entry_price_cents": config.max_entry_price_cents,
        },
        "detection": {
            "suspected_high_f": state.suspected_high,
            "suspected_high_time_utc": (
                state.suspected_high_time.isoformat() if state.suspected_high_time else None
            ),
            "drop_detected": state.drop_detected,
            "drop_temp_f": state.drop_temp,
            "drop_time_utc": state.drop_time.isoformat() if state.drop_time else None,
            "drop_persist_count": state.drop_persist_count,
        },
        "triple_lock": {
            "lock1_inferred_pass": lock1_inferred_pass,
            "lock1_ceiling_f": state.morning_model_high,
            "lock2_inferred_pass": lock2_inferred_pass,
            "lock2_nws_obs_f": state.wethr_high,
            "lock2_diff_f": lock2_diff,
            "triple_lock_passed": state.triple_lock_passed,
        },
        "settlement_audit": {
            "predicted_settlement_f": state.predicted_settlement_f,
            "confidence": state.settlement_confidence,
        },
        "ground_truth": {
            "cli_confirmed": state.dsm_confirmed,
            "cli_high_f": state.dsm_max_temp,
            "dsm_hold_count": state.dsm_hold_count,
            "bracket_low": state.kalshi_bracket_low,
            "bracket_high": bracket_high_serialised,
            "bracket_correct": bracket_correct,
            "settlement_prediction_correct": settlement_prediction_correct,
        },
        "economics": {
            "kalshi_ticker": state.kalshi_ticker,
            "price_at_settlement_audit": price_at_settlement_audit,
            "price_at_confirmation": price_at_confirmation,
            "potential_profit_cents": potential_profit_cents,
            "tradeable": tradeable,
            "price_history": prices_clean,
        },
        "alerts": {
            "drop_alert_fired": state.drop_alert_fired,
            "alert_fired": state.alert_fired,
            "dsm_timeout_fired": state.dsm_timeout_fired,
        },
    }

    path = BACKTEST_DIR / f"{state.date.isoformat()}_{station}.json"
    path.write_text(json.dumps(record, indent=2))
    logger.info("Backtest record written: %s", path)
