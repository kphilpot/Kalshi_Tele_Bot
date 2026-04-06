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
import math
from datetime import datetime
from pathlib import Path

from config import BACKTEST_RISK_PCT, BACKTEST_STARTING_BANK, CityConfig
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

def _load_opening_balance(today_date) -> float:
    """Return the compounded account balance at the start of today.

    Sums actual_pnl from all records dated strictly before today.
    All 3 cities on the same day share the same opening balance.
    """
    if not BACKTEST_DIR.exists():
        return BACKTEST_STARTING_BANK
    total = 0.0
    today_str = today_date.isoformat()
    for path in BACKTEST_DIR.glob("*.json"):
        try:
            rec = json.loads(path.read_text())
            if rec["meta"]["date"] < today_str:
                total += rec["economics"].get("actual_pnl", 0.0) or 0.0
        except Exception:
            pass
    return round(BACKTEST_STARTING_BANK + total, 2)


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
    candidates = [p for p in [price_at_settlement_audit, price_at_confirmation] if p is not None]
    entry_price = min(candidates) if candidates else None

    # ── Price threshold crossing ($0.75) ─────────────────────────────────────
    price_crossed_75_cents: bool | None = None
    price_crossed_75_cents_time_utc: str | None = None
    price_crossed_75_cents_value: float | None = None

    if state.price_above_75_cents:
        price_crossed_75_cents = True
        price_crossed_75_cents_time_utc = (
            state.price_above_75_cents_time.isoformat()
            if state.price_above_75_cents_time else None
        )
        price_crossed_75_cents_value = state.price_above_75_cents_value

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
                bracket_correct = actual < lo  # "between X and Y": YES if temp < X

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
    # Opening balance compounds from all prior days (same for all 3 cities on the same day)
    opening_balance = _load_opening_balance(state.date)
    stake_dollars = round(opening_balance * BACKTEST_RISK_PCT, 2)

    potential_profit_cents: int | None = None
    tradeable: bool | None = None
    contracts: int = 0
    net_pnl_win: float = 0.0
    net_pnl_loss: float = 0.0
    actual_pnl: float = 0.0
    trade_outcome: str = "no_trade"

    if entry_price is not None:
        potential_profit_cents = round((1.00 - entry_price) * 100)
        tradeable = entry_price <= (config.max_entry_price_cents / 100)

        if tradeable and entry_price > 0:
            contracts = math.floor(stake_dollars / entry_price)
            net_pnl_win = round(contracts * (1.0 - entry_price), 2)
            net_pnl_loss = round(-stake_dollars, 2)

            if bracket_correct is True:
                actual_pnl = net_pnl_win
                trade_outcome = "win"
            elif bracket_correct is False:
                actual_pnl = net_pnl_loss
                trade_outcome = "loss"
            else:
                trade_outcome = "pending"  # CLI not confirmed yet

    # ── Serialise bracket bounds (inf/-inf are not valid JSON) ─────────────
    bracket_low_serialised = (
        None
        if state.kalshi_bracket_low in (float("-inf"), None)
        else state.kalshi_bracket_low
    )
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
            "bracket_low": bracket_low_serialised,
            "bracket_high": bracket_high_serialised,
            "bracket_correct": bracket_correct,
            "settlement_prediction_correct": settlement_prediction_correct,
            "tgroup_gap_f": state.tgroup_gap_f,
        },
        "bracket_lookup": {
            "settlement_audit": {
                "found": state.settlement_audit_bracket_found,
                "bracket_low": state.settlement_audit_bracket_low,
                "bracket_high": state.settlement_audit_bracket_high,
                "failure_reason": state.settlement_audit_failure_reason,
            },
            "confirmation": {
                "found": state.confirmation_bracket_found,
                "bracket_low": bracket_low_serialised,
                "bracket_high": bracket_high_serialised,
                "failure_reason": state.confirmation_failure_reason,
            },
        },
        "economics": {
            "kalshi_ticker": state.kalshi_ticker,
            "price_at_settlement_audit": price_at_settlement_audit,
            "price_at_confirmation": price_at_confirmation,
            "price_crossed_75_cents": price_crossed_75_cents,
            "price_crossed_75_cents_time_utc": price_crossed_75_cents_time_utc,
            "price_crossed_75_cents_value": price_crossed_75_cents_value,
            "potential_profit_cents": potential_profit_cents,
            "tradeable": tradeable,
            "opening_balance_dollars": opening_balance,
            "stake_dollars": stake_dollars,
            "contracts": contracts,
            "net_pnl_win": net_pnl_win,
            "net_pnl_loss": net_pnl_loss,
            "actual_pnl": actual_pnl,
            "trade_outcome": trade_outcome,
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

    # Log T-Group divergence
    _log_tgroup_divergence(station, state, config)

    # Log bracket failures
    _log_bracket_failures(station, state, config)


def _log_bracket_failures(station: str, state: DailyState, config: CityConfig) -> None:
    """
    Append bracket lookup failures to a persistent forensics log.
    Creates state/bracket_failures.json if it doesn't exist.
    Logs both settlement audit and confirmation failures.
    """
    failures_to_log = []

    # Check settlement audit failure
    if not state.settlement_audit_bracket_found and state.settlement_audit_failure_reason:
        failures_to_log.append({
            "phase": "settlement_audit",
            "temp_searched": state.predicted_settlement_f,
            "reason": state.settlement_audit_failure_reason,
        })

    # Check confirmation failure
    if not state.confirmation_bracket_found and state.confirmation_failure_reason:
        failures_to_log.append({
            "phase": "confirmation",
            "temp_searched": state.dsm_max_temp,
            "reason": state.confirmation_failure_reason,
        })

    if not failures_to_log:
        return

    failures_path = Path("state") / "bracket_failures.json"
    failures_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing entries or create new list
    entries = []
    if failures_path.exists():
        try:
            data = json.loads(failures_path.read_text())
            entries = data.get("failures", [])
        except Exception as exc:
            logger.warning("Could not load existing bracket failures log: %s", exc)

    # Create new entries for each failure
    for failure in failures_to_log:
        new_entry = {
            "date": state.date.isoformat(),
            "station": station,
            "city": config.display_name,
            "tgroup_predicted": state.predicted_settlement_f,
            "cli_confirmed": state.dsm_max_temp,
            "tgroup_gap_f": state.tgroup_gap_f,
            "phase": failure["phase"],
            "temp_searched": failure["temp_searched"],
            "failure_reason": failure["reason"],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        entries.append(new_entry)

    # Write back to file
    try:
        failures_path.write_text(json.dumps({"failures": entries}, indent=2))
        logger.info("Bracket failure(s) logged for %s: %d failure(s)", station, len(failures_to_log))
    except Exception as exc:
        logger.error("Failed to write bracket failures log: %s", exc)


def _log_tgroup_divergence(station: str, state: DailyState, config: CityConfig) -> None:
    """
    Append T-Group divergence (gap between prediction and CLI confirmed) to a persistent log.
    Creates state/tgroup_divergence.json if it doesn't exist.
    Only logs if both predicted_settlement_f and dsm_confirmed are available.
    """
    if state.predicted_settlement_f is None or not state.dsm_confirmed or state.dsm_max_temp is None:
        return

    divergence_path = Path("state") / "tgroup_divergence.json"
    divergence_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing entries or create new list
    entries = []
    if divergence_path.exists():
        try:
            data = json.loads(divergence_path.read_text())
            entries = data.get("entries", [])
        except Exception as exc:
            logger.warning("Could not load existing divergence log: %s", exc)

    # Calculate gap and direction
    gap_f = state.dsm_max_temp - state.predicted_settlement_f
    direction = "over" if gap_f > 0 else ("under" if gap_f < 0 else "exact")

    # Create new entry
    new_entry = {
        "date": state.date.isoformat(),
        "station": station,
        "city": config.display_name,
        "tgroup_predicted_f": state.predicted_settlement_f,
        "cli_confirmed_f": state.dsm_max_temp,
        "gap_f": round(gap_f, 1),
        "direction": direction,
        "confidence": state.settlement_confidence,
    }

    entries.append(new_entry)

    # Write back to file
    try:
        divergence_path.write_text(json.dumps({"entries": entries}, indent=2))
        logger.info("T-Group divergence logged: %s (gap: %.1f°F %s)", station, gap_f, direction)
    except Exception as exc:
        logger.error("Failed to write T-Group divergence log: %s", exc)
