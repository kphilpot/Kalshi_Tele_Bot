"""
alerts.py — Pure message-formatting functions.

No I/O.  Every function accepts state + config objects and returns a plain string
ready to send via Telegram (no HTML/Markdown markup — plain text for maximum
compatibility and readability on all Telegram clients).
"""

from datetime import date, datetime, timedelta
from typing import Optional

import pytz

from config import CityConfig, PRICE_FLAG_THRESHOLD, ERROR_LOG_PRUNE_MINUTES
from state import DailyState
from weather import CLIResult, ConfidenceLevel, SettlementAuditor

# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

EST = pytz.timezone("America/New_York")


def _to_est(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(EST)


def _fmt_dt(dt: Optional[datetime], tz: pytz.BaseTzInfo) -> str:
    """Format a UTC datetime as 'H:MM AM/PM TZabbr' (cross-platform, no leading zero)."""
    if dt is None:
        return "unknown"
    local = dt.astimezone(tz) if dt.tzinfo else pytz.utc.localize(dt).astimezone(tz)
    h = local.hour % 12 or 12
    return f"{h}:{local.strftime('%M %p %Z').strip()}"


def _fmt_dt_both(dt: Optional[datetime], city_tz_str: str) -> str:
    """
    Format a UTC datetime in both local city time and EST.
    Returns e.g. '2:14 PM CST (3:14 PM EST)' or just '2:14 PM EST' if same zone.
    """
    if dt is None:
        return "unknown"
    city_tz = pytz.timezone(city_tz_str)
    local_str = _fmt_dt(dt, city_tz)
    est_str = _fmt_dt(dt, EST)

    # If city is already EST, don't duplicate
    city_tz_now = datetime.now(city_tz)
    est_now = datetime.now(EST)
    if city_tz_now.utcoffset() == est_now.utcoffset():
        return est_str

    return f"{local_str} ({est_str})"


def _time_remaining(close_time: Optional[datetime]) -> str:
    """Return a human-readable countdown to *close_time*."""
    if close_time is None:
        return "unknown"
    now = datetime.now(pytz.utc)
    ct = close_time if close_time.tzinfo else pytz.utc.localize(close_time)
    delta = ct - now
    if delta.total_seconds() <= 0:
        return "CLOSED"
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes = rem // 60
    if hours > 0:
        return f"{hours}h {minutes}m remaining"
    return f"{minutes}m remaining"


def _fmt_date(d: date) -> str:
    # %-d is Linux-only; use str(d.day) for cross-platform no-leading-zero day
    return d.strftime("%A %B ") + str(d.day) + d.strftime(", %Y")


def _fmt_price(price: Optional[float]) -> str:
    """Format price as 'XX¢ ($0.XX)'."""
    if price is None:
        return "unavailable"
    cents = round(price * 100)
    return f"{cents}¢ (${price:.2f})"


def _fmt_bracket(low: Optional[float], high: Optional[float]) -> str:
    if low is None or high is None:
        return "unknown"
    if low == float("-inf"):
        return f"below {high:.0f}°F"
    if high == float("inf"):
        return f"above {low:.0f}°F"
    return f"{low:.0f}–{high:.0f}°F"


# ---------------------------------------------------------------------------
# Error log formatting
# ---------------------------------------------------------------------------

def format_error_log(
    error_log: list,
    max_age_minutes: int = ERROR_LOG_PRUNE_MINUTES,
) -> str:
    if not error_log:
        return "No errors."
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    recent = [(s, t, m) for s, t, m in error_log if t > cutoff]
    if not recent:
        return "No recent errors."
    lines = [f"  [{s} @ {t.strftime('%H:%M')} UTC] {m}" for s, t, m in recent]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trade flag logic
# ---------------------------------------------------------------------------

def _trade_flags(
    price: Optional[float],
    bracket_low: Optional[float],
    bracket_high: Optional[float],
    confirmed_high: Optional[float],
    max_price: float = PRICE_FLAG_THRESHOLD,
) -> str:
    """
    Returns a trade-flag string based on price and bracket position.

    confirmed_high == bracket_low means the high is at the LOWER end of the
    bracket (e.g., bracket 83-84, confirmed high = 83) — elevated resolution risk.

    max_price is per-city (from CityConfig.max_entry_price_cents / 100).
    """
    flags = []

    if price is not None and price > max_price:
        flags.append(
            f"TRADE NOT ADVISED — Market above {round(max_price * 100):.0f}¢"
        )

    if (
        confirmed_high is not None
        and bracket_low is not None
        and confirmed_high == bracket_low
    ):
        flags.append(
            "TRADE NOT ADVISED — Confirmed high at lower end of bracket, resolution risk elevated"
        )

    if len(flags) == 2:
        combined = (
            "TRADE NOT ADVISED — Two flags: "
            + f"price above {round(max_price * 100):.0f}¢ AND bracket risk"
        )
        return f"⚠️  {combined}"

    if flags:
        return f"⚠️  {flags[0]}"

    return "✅  No flags — trade within normal parameters"


# ---------------------------------------------------------------------------
# Morning message
# ---------------------------------------------------------------------------

def format_morning_message(
    cli_results: dict,   # station -> CLIResult | None
    cli_errors: dict,    # station -> error_string | None
    forecast_results: dict,  # station -> ForecastResult | None
    forecast_errors: dict,   # station -> error_string | None
    configs: dict,       # station -> CityConfig
    today: date,
) -> str:
    from weather import ForecastResult  # local import to avoid circular

    lines = [
        "☀️  DAILY PEAK WATCH",
        f"Date: {_fmt_date(today)}",
        "",
    ]

    for station, config in configs.items():
        cli = cli_results.get(station)
        err = cli_errors.get(station)
        forecast = forecast_results.get(station)
        f_err = forecast_errors.get(station)

        lines.append(f"{'─' * 40}")
        lines.append(f"{config.station} ({config.display_name})")
        lines.append("")

        # Today's forecast
        if forecast is not None:
            lines.append(f"  TODAY'S FORECAST HIGH: {forecast.high_f:.0f}°F")
            lines.append(f"  Conditions: {forecast.short_forecast}")
        elif f_err:
            lines.append(f"  Forecast unavailable: {f_err}")
        else:
            lines.append("  Forecast: not yet available")

        # Yesterday's actual
        if cli is not None:
            high_str = f"{cli.yesterday_high_f:.0f}°F"
            time_annotation = "(LST)" if cli.time_str != "unknown" else ""
            lines.append(f"  Yesterday's actual: {high_str} at {cli.time_str} {time_annotation}".strip())
            if cli.normal_high:
                lines.append(f"  Normal high: {cli.normal_high:.0f}°F")
        elif err:
            lines.append(f"  Yesterday's data unavailable: {err}")

        lines.append(
            f"  Poll window: 12:00 PM local → 10:00 PM EST  (every 10 min)"
        )
        lines.append("")

    lines.append(f"{'─' * 40}")
    lines.append("")
    lines.append("You'll get a separate text per city when the peak is detected,")
    lines.append("then a Settlement Prediction with early bracket (HIGH) or Rounding Trap warning (WARNING),")
    lines.append("then a CLI Scorecard at ~7-8 PM local confirming the official settlement.")
    lines.append("")
    lines.append("Send /dispatch for manual status check.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Drop-detected alert (afternoon, per-city)
# ---------------------------------------------------------------------------

def format_drop_detected_alert(
    state: DailyState,
    config: CityConfig,
    forecast_high: Optional[float] = None,
) -> str:
    """
    Sent per-city when the temperature drops below the suspected peak.
    This is the afternoon "heads up" — CLI confirmation comes later.
    """
    city_tz = pytz.timezone(config.tz)

    recent_metars = state.metar_readings[-5:] if state.metar_readings else []
    metar_trend = ", ".join(
        f"{temp:.0f}°F" for _, temp in recent_metars
    ) if recent_metars else "unavailable"

    lines = [
        f"📉  PEAK DETECTED — {config.display_name}",
        f"Station: {config.station}",
        f"Date: {_fmt_date(state.date)}",
        "",
        f"Suspected high: {state.suspected_high:.0f}°F"
        if state.suspected_high else "Suspected high: unknown",
        f"Peak time: {_fmt_dt_both(state.suspected_high_time, config.tz)}"
        if state.suspected_high_time else "",
        "",
        f"Current temp: {state.drop_temp:.0f}°F (dropping)"
        if state.drop_temp else "",
        f"Drop time: {_fmt_dt_both(state.drop_time, config.tz)}"
        if state.drop_time else "",
        "",
        f"METAR trend: {metar_trend}",
    ]

    if forecast_high is not None:
        diff = abs(state.suspected_high - forecast_high) if state.suspected_high else 0
        lines.append(f"Forecast high was: {forecast_high:.0f}°F (diff: {diff:.0f}°F)")

    lines.append("")
    lines.append("Settlement Prediction will follow shortly (T-Group audit).")
    lines.append("CLI Scorecard with verified settlement at ~7-8 PM local.")

    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# Settlement Audit alert (T-Group — fires shortly after drop alert)
# ---------------------------------------------------------------------------

def format_settlement_audit_alert(
    state: DailyState,
    config: CityConfig,
    early_ticker: Optional[str] = None,
    early_bracket_low: Optional[float] = None,
    early_bracket_high: Optional[float] = None,
    early_price: Optional[float] = None,
) -> str:
    """
    Sent per-city right after the drop alert, once the T-Group Settlement Audit runs.

    HIGH    — T-Group agrees; early bracket prediction included.
    CAUTION — Rounding edge (0.5–1.0°F drift); MIA/ORD small position OK, AUS wait for CLI.
    WARNING — Bracket likely shifts (>1.0°F drift); do not enter until CLI confirms.
    FAIL_OPEN should not reach this function — caller guards it.
    """
    confidence = state.settlement_confidence or ConfidenceLevel.FAIL_OPEN.value
    predicted = state.predicted_settlement_f
    suspected = state.suspected_high

    # Signed drift: positive = T-Group higher than METAR, negative = T-Group lower
    signed_drift = (predicted - suspected) if predicted is not None and suspected is not None else 0.0
    drift = abs(signed_drift)
    direction = "↑ HIGHER" if signed_drift > 0 else ("↓ LOWER" if signed_drift < 0 else "= SAME")

    predicted_str = f"{predicted:.0f}°F" if predicted is not None else "unknown"
    suspected_str = f"{suspected:.0f}°F" if suspected is not None else "unknown"
    drift_str     = f"{drift:.2f}°F" if drift is not None else "unknown"

    # ── HIGH ─────────────────────────────────────────────────────────────────
    if confidence == ConfidenceLevel.HIGH.value:
        lines = [
            f"📐  SETTLEMENT PREDICTION — {config.display_name}",
            f"Station: {config.station}",
            "",
            f"Suspected high:   {suspected_str}",
            f"T-Group predicts: {predicted_str}  {direction}",
            f"Drift:            {drift_str}  ✅ Within {SettlementAuditor.DRIFT_HIGH_THRESHOLD_F}°F threshold",
            "",
        ]
        if early_ticker:
            lines += [
                "─" * 40,
                "📊  Early Bracket Prediction",
                f"    Ticker: {early_ticker}",
                f"🎯  Predicted bracket: {_fmt_bracket(early_bracket_low, early_bracket_high)} YES",
                f"💰  Current YES ask: {_fmt_price(early_price)}",
                "",
                _trade_flags(early_price, early_bracket_low, early_bracket_high, predicted,
                             max_price=config.max_entry_price_cents / 100),
                "",
            ]
            if config.station == "KAUS":
                lines.append("⚠️  Austin — T-Group accuracy ~85-88%. Wait for CLI before trading.")
            else:
                lines.append("✅  MIA/ORD — T-Group accuracy >92%. Early entry is reasonable.")
        else:
            lines += [
                "⚠️  No matching Kalshi bracket found for predicted settlement.",
                "    Manual bracket lookup required.",
            ]
        lines += ["", "CLI scorecard will follow at ~7-8 PM local to verify settlement."]
        return "\n".join(l for l in lines if l is not None)

    # ── CAUTION ───────────────────────────────────────────────────────────────
    # Austin always escalates CAUTION → WARNING (solar spike risk too high to risk entry)
    elif confidence == ConfidenceLevel.CAUTION.value and config.station != "KAUS":
        action = (
            "MIA/ORD — CAUTION is rare here (>92% base accuracy). "
            "Small position (25–50% normal size) is acceptable. "
            "CLI will confirm."
        )
        action_icon = "⚡"

        lines = [
            f"⚡  ROUNDING EDGE — {config.display_name}",
            f"Station: {config.station}",
            "",
            f"Suspected high:   {suspected_str}",
            f"T-Group predicts: {predicted_str}  {direction}",
            f"Drift:            {drift_str}  ({SettlementAuditor.DRIFT_HIGH_THRESHOLD_F}–"
            f"{SettlementAuditor.DRIFT_CAUTION_THRESHOLD_F}°F range)",
            "",
            "Settlement is within 1°F of suspected high.",
            "Same bracket is likely but not guaranteed.",
            "",
            "─" * 40,
            f"{action_icon}  {action}",
            "",
            "CLI scorecard will confirm settlement at ~7-8 PM local.",
        ]
        return "\n".join(lines)

    # ── WARNING ───────────────────────────────────────────────────────────────
    else:
        # Describe which direction the risk runs
        if signed_drift > 0:
            risk_note = (
                f"T-Group is tracking {drift:.0f}°F ABOVE suspected high. "
                f"Settlement may be in a higher bracket than expected — "
                f"check bracket around {predicted_str}."
            )
        else:
            risk_note = (
                f"T-Group is tracking {drift:.0f}°F BELOW suspected high. "
                f"Settlement may be in a lower bracket than expected — "
                f"check bracket around {predicted_str}."
            )

        city_note = (
            "Austin: Solar spike risk compounds this. "
            "High probability of wrong bracket."
            if config.station == "KAUS"
            else f"{config.display_name}: CAUTION is rare here — WARNING is a strong signal."
        )

        lines = [
            f"⚠️  ROUNDING TRAP — {config.display_name}",
            f"Station: {config.station}",
            "",
            f"Suspected high:   {suspected_str}",
            f"T-Group predicts: {predicted_str}  {direction}",
            f"Drift:            {drift_str}  ⛔ ABOVE {SettlementAuditor.DRIFT_CAUTION_THRESHOLD_F}°F threshold",
            "",
            risk_note,
            "",
            city_note,
            "",
            "─" * 40,
            "⛔  DO NOT TRADE — Wait for CLI confirmation (~7-8 PM local).",
            "    Entering now risks the wrong bracket entirely.",
            "",
            "CLI scorecard will confirm the correct settlement and bracket.",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Confirmation alert
# ---------------------------------------------------------------------------

def format_confirmation_alert(
    state: DailyState,
    config: CityConfig,
    hold_count: int = 0,
) -> str:
    city_tz = pytz.timezone(config.tz)

    # METAR last 3 temps
    recent_metars = state.metar_readings[-3:] if state.metar_readings else []
    metar_summary = ", ".join(
        f"{temp:.0f}°F" for _, temp in reversed(recent_metars)
    ) if recent_metars else "unavailable"

    # Close time
    close_est = _to_est(state.kalshi_close_time)
    close_str = (
        f"{close_est.strftime('%I:%M %p EST')} ({_time_remaining(state.kalshi_close_time)})"
        if close_est else "unknown"
    )

    hold_note = ""
    if hold_count > 0:
        hold_note = f"\n  (Alert held {hold_count} poll cycle(s) awaiting DSM confirmation)"

    # Check if T-Group prediction matched actual settlement
    settlement_match = ""
    if state.predicted_settlement_f is not None and state.dsm_max_temp is not None:
        diff = abs(state.predicted_settlement_f - state.dsm_max_temp)
        if diff < 0.5:
            settlement_match = f"✅  T-Group predicted: {state.predicted_settlement_f:.0f}°F — CORRECT"
        else:
            settlement_match = (
                f"⚠️  T-Group predicted: {state.predicted_settlement_f:.0f}°F, "
                f"actual: {state.dsm_max_temp:.0f}°F (off by {diff:.0f}°F)"
            )

    lines = [
        "📊  CLI SETTLEMENT VERIFIED",
        f"Station: {config.station} ({config.display_name})",
        f"Date: {_fmt_date(state.date)}",
        f"Time (EST): {_fmt_dt(datetime.utcnow().replace(tzinfo=pytz.utc), EST)}",
        hold_note,
        "",
        f"🌡  NWS CLI official high: {state.dsm_max_temp:.0f}°F"
        if state.dsm_confirmed else f"🌡  Suspected high: {state.suspected_high:.0f}°F",
        f"    Occurred at: {_fmt_dt_both(state.suspected_high_time, config.tz)}"
        if state.suspected_high_time else "",
        settlement_match,
        "",
        f"📉  Drop detected: {state.drop_temp:.0f}°F"
        if state.drop_temp else "📉  Drop: not yet detected",
        f"    Drop time: {_fmt_dt_both(state.drop_time, config.tz)}"
        if state.drop_time else "",
        "",
        f"✅  METAR last readings: {metar_summary} — declining",
        "",
        "─" * 40,
        "📊  Kalshi Settlement Bracket",
    ]

    # Strip empty strings from lines
    lines = [l for l in lines if l is not None]

    if state.kalshi_ticker:
        lines.append(f"    Ticker: {state.kalshi_ticker}")
        lines.append(
            f"🎯  Verified bracket: {_fmt_bracket(state.kalshi_bracket_low, state.kalshi_bracket_high)} YES"
        )
        lines.append(f"💰  Current YES ask: {_fmt_price(state.kalshi_price)}")
        lines.append(f"⏰  Market closes: {close_str}")
        lines.append("")

        flag_line = _trade_flags(
            state.kalshi_price,
            state.kalshi_bracket_low,
            state.kalshi_bracket_high,
            state.dsm_max_temp if state.dsm_confirmed else state.suspected_high,
            max_price=config.max_entry_price_cents / 100,
        )
        lines.append(flag_line)
    else:
        lines.append(
            "⚠️  BRACKET WARNING — Could not identify a clean matching bracket on Kalshi."
        )
        lines.append("    Manual review required before trading.")

    # Error log
    err_str = format_error_log(state.error_log)
    if err_str != "No errors.":
        lines.append("")
        lines.append("Recent errors:")
        lines.append(err_str)

    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# DSM timeout alert
# ---------------------------------------------------------------------------

def format_dsm_timeout_alert(state: DailyState, config: CityConfig) -> str:
    city_tz = pytz.timezone(config.tz)

    lines = [
        "⚠️  DSM TIMEOUT",
        f"Station: {config.station} ({config.display_name})",
        f"Date: {_fmt_date(state.date)}",
        "",
        f"DSM has NOT confirmed the suspected high of {state.suspected_high:.0f}°F"
        if state.suspected_high else "DSM has NOT confirmed a suspected high",
        f"Timeout threshold: {config.dsm_timeout_local.hour % 12 or 12}:{config.dsm_timeout_local.strftime('%M %p')} local",
        "",
        f"Suspected high identified at:"
        f" {_fmt_dt_both(state.suspected_high_time, config.tz)}"
        if state.suspected_high_time else "Suspected high time: unknown",
        "",
        "No confirmation alert will fire until DSM updates.",
        "Manual monitoring recommended.",
    ]

    err_str = format_error_log(state.error_log)
    if err_str != "No errors.":
        lines.append("")
        lines.append("Recent errors:")
        lines.append(err_str)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /dispatch manual trigger response
# ---------------------------------------------------------------------------

def format_dispatch_response(
    states: dict,    # station -> DailyState
    configs: dict,   # station -> CityConfig
    metar_summaries: dict,  # station -> list[(datetime, float)] most recent 3
    dsm_statuses: dict,     # station -> str (status description)
    lock_statuses: dict | None = None,  # station -> str (Triple-Lock status)
) -> str:
    """
    Combined manual dispatch message for all three cities.
    Includes ALL-CAPS warning header per spec.
    """
    lines = [
        "THIS WAS A MANUAL TRIGGER. DATA IS NOT CONFIRMED BY BOT CONDITIONS. VERIFY BEFORE TRADING.",
        "",
        f"Manual dispatch run at {datetime.now(EST).strftime('%I:%M %p EST')}",
        "",
    ]

    for station, config in configs.items():
        state = states.get(station)
        city_tz = pytz.timezone(config.tz)

        lines.append("═" * 45)
        lines.append(f"{config.station} ({config.display_name})")
        lines.append("")

        if state is None:
            lines.append("  No state data available.")
            continue

        # Time Series peak
        if state.suspected_high is not None:
            lines.append(
                f"  Time Series peak: {state.suspected_high:.0f}°F"
                f" at {_fmt_dt_both(state.suspected_high_time, config.tz)}"
            )
        else:
            lines.append("  Time Series peak: not yet detected")

        # METAR last readings
        recent = metar_summaries.get(station, [])
        if recent:
            temps = ", ".join(f"{t:.0f}°F" for _, t in reversed(recent[-3:]))
            lines.append(f"  METAR last readings: {temps}")
        else:
            lines.append("  METAR: no readings available")

        # Drop status
        if state.drop_detected:
            lines.append(
                f"  Drop detected: {state.drop_temp:.0f}°F"
                f" at {_fmt_dt_both(state.drop_time, config.tz)}"
                if state.drop_temp else "  Drop detected"
            )
        else:
            lines.append("  Drop: not yet detected")

        # DSM
        dsm_status = dsm_statuses.get(station, "unknown")
        lines.append(f"  DSM status: {dsm_status}")

        # Kalshi
        if state.kalshi_ticker:
            lines.append(f"  Kalshi ticker: {state.kalshi_ticker}")
            lines.append(
                f"  Bracket: {_fmt_bracket(state.kalshi_bracket_low, state.kalshi_bracket_high)}"
            )
            lines.append(f"  YES ask: {_fmt_price(state.kalshi_price)}")
        else:
            lines.append("  Kalshi: bracket not yet identified")

        # Alert status
        lines.append(f"  Alert fired: {'YES' if state.alert_fired else 'No'}")
        lines.append(f"  DSM timeout fired: {'YES' if state.dsm_timeout_fired else 'No'}")

        # Triple-Lock status
        if lock_statuses:
            lock_str = lock_statuses.get(station, "N/A")
            lines.append(f"  Triple-Lock: {lock_str}")

        # Errors
        err_str = format_error_log(state.error_log)
        if err_str not in ("No errors.", "No recent errors."):
            lines.append("  Recent errors:")
            for eline in err_str.splitlines():
                lines.append(f"  {eline}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# End-of-day summary
# ---------------------------------------------------------------------------

def format_eod_summary(
    states: dict,   # station -> DailyState
    configs: dict,  # station -> CityConfig
    today: date,
) -> str:
    lines = [
        "📋  END OF DAY SUMMARY",
        f"Date: {_fmt_date(today)}",
        "",
    ]

    for station, config in configs.items():
        state = states.get(station)

        lines.append("─" * 40)
        lines.append(f"{config.station} ({config.display_name})")

        if state is None:
            lines.append("  No data recorded today.")
            continue

        # High temp
        if state.dsm_confirmed and state.dsm_max_temp is not None:
            lines.append(f"  Confirmed high: {state.dsm_max_temp:.0f}°F")
            bracket = _fmt_bracket(state.kalshi_bracket_low, state.kalshi_bracket_high)
            lines.append(f"  Bracket that should have been taken: {bracket} YES")
        elif state.suspected_high is not None:
            lines.append(f"  Suspected high: {state.suspected_high:.0f}°F (DSM never confirmed)")
        else:
            lines.append("  No temperature peak detected today")

        # Alert status
        if state.alert_fired:
            alert_time = _fmt_dt(state.drop_time, EST) if state.drop_time else "unknown time"
            lines.append(f"  Alert fired: YES at {alert_time}")
            lines.append(f"  YES ask at alert time: {_fmt_price(state.kalshi_price)}")

            # Trade advised?
            flag = _trade_flags(
                state.kalshi_price,
                state.kalshi_bracket_low,
                state.kalshi_bracket_high,
                state.dsm_max_temp if state.dsm_confirmed else state.suspected_high,
                max_price=config.max_entry_price_cents / 100,
            )
            lines.append(f"  Trade flag: {flag}")
        else:
            lines.append("  Alert fired: No")
            if state.dsm_timeout_fired:
                lines.append("  DSM timeout alert fired: YES")
                lines.append(f"  Reason: DSM never updated to match Time Series peak")

        lines.append("")

    lines.append("─" * 40)
    lines.append("CLI resolution data will appear in tomorrow's morning message.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2 PM EST afternoon pulse — one daily check-in, all three cities
# ---------------------------------------------------------------------------

def format_afternoon_pulse(
    states: dict,   # station -> DailyState
    configs: dict,  # station -> CityConfig
) -> str:
    """
    Single mid-afternoon check-in sent at 2 PM EST.
    One status line per city — tells the user the bot is alive and what each
    city is doing without requiring any action.
    """
    now_est = datetime.now(EST)
    time_str = f"{now_est.hour % 12 or 12}:{now_est.strftime('%M %p EST')}"

    lines = [
        f"🕑  AFTERNOON CHECK-IN — {time_str}",
        "",
    ]

    for station, config in configs.items():
        state = states.get(station)
        city_tz = pytz.timezone(config.tz)

        lines.append(f"{config.station} ({config.display_name})")

        if state is None:
            lines.append("  No data available.")
            lines.append("")
            continue

        # Case 1: Full alert already fired (CLI confirmed or drop alert sent)
        if state.alert_fired:
            settled = state.dsm_max_temp if state.dsm_confirmed else state.suspected_high
            settled_str = f"{settled:.0f}°F" if settled is not None else "unknown"
            bracket_str = (
                f" — bracket {_fmt_bracket(state.kalshi_bracket_low, state.kalshi_bracket_high)}"
                if state.kalshi_ticker else ""
            )
            lines.append(f"  ✅  Alert fired — settled {settled_str}{bracket_str}. Done for today.")

        # Case 2: Drop detected, awaiting CLI (drop_alert_fired but not full alert_fired)
        elif state.drop_alert_fired:
            high_str = f"{state.suspected_high:.0f}°F" if state.suspected_high else "unknown"
            drop_time_str = _fmt_dt_both(state.drop_time, config.tz) if state.drop_time else "unknown"
            lines.append(f"  📉  Peak at {high_str} — drop detected at {drop_time_str}.")
            lines.append("      Waiting for CLI confirmation (~7-8 PM local).")

        # Case 3: Drop detected but persist gate holding (or Triple-Lock not yet passed)
        elif state.drop_detected:
            high_str = f"{state.suspected_high:.0f}°F" if state.suspected_high else "unknown"
            lines.append(f"  📉  Peak at {high_str} — drop seen, validating (persist gate active).")

        # Case 4: Suspected high found, no drop yet
        elif state.suspected_high is not None:
            high_str = f"{state.suspected_high:.0f}°F"
            peak_time_str = _fmt_dt_both(state.suspected_high_time, config.tz)
            lines.append(f"  📈  Peak so far: {high_str} at {peak_time_str} — no drop yet.")

        # Case 5: Nothing yet
        else:
            lines.append("  ⏳  No peak detected yet — still in early window.")

        # Show settlement confidence if available (T-Group ran but CLI not yet confirmed)
        if state.settlement_confidence and state.settlement_confidence != "FAIL_OPEN" and not state.alert_fired:
            conf = state.settlement_confidence
            pred_str = f"{state.predicted_settlement_f:.0f}°F" if state.predicted_settlement_f else "unknown"
            icon = "✅" if conf == "HIGH" else ("⚡" if conf == "CAUTION" else "⚠️")
            lines.append(f"      T-Group: {icon} {conf} — predicted settlement {pred_str}.")

        # DSM timeout note
        if state.dsm_timeout_fired and not state.alert_fired:
            lines.append("      ⏰  DSM timeout fired — no CLI confirmation yet.")

        lines.append("")

    lines.append("Send /dispatch for a full data snapshot.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /status command (compact single-message status)
# ---------------------------------------------------------------------------

def format_status(
    states: dict,
    configs: dict,
) -> str:
    lines = [
        f"Bot status — {datetime.now(EST).hour % 12 or 12}:{datetime.now(EST).strftime('%M %p')} EST",
        "",
    ]

    for station, config in configs.items():
        state = states.get(station)
        lines.append(f"{config.station} ({config.display_name})")
        if state is None:
            lines.append("  No state")
            continue

        high_str = (
            f"{state.suspected_high:.0f}°F" if state.suspected_high else "none"
        )
        lines.append(f"  Suspected high: {high_str}")
        lines.append(f"  Drop detected: {'yes' if state.drop_detected else 'no'}")
        lines.append(f"  DSM confirmed: {'yes' if state.dsm_confirmed else 'no'}")
        lines.append(f"  Alert fired: {'yes' if state.alert_fired else 'no'}")
        if state.kalshi_ticker:
            lines.append(f"  Kalshi: {state.kalshi_ticker} | {_fmt_price(state.kalshi_price)}")

    return "\n".join(lines)
