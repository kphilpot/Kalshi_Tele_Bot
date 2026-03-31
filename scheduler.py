"""
scheduler.py — APScheduler setup and poll orchestration.

Jobs:
  morning_job     — 8:00 AM EST: fetch CLI for all cities, send morning message
  poll_kaus_job   — every 10 min from 1 PM EST: window-guarded poll for KAUS
  poll_kmia_job   — every 10 min from 12 PM EST: window-guarded poll for KMIA
  poll_kmdw_job   — every 10 min from 1 PM EST: window-guarded poll for KMDW
  eod_job         — 10:00 PM EST: send end-of-day summary
  midnight_job    — 12:00 AM EST: reset all state

Core logic:
  run_poll_cycle  — full 8-step state machine for one city per 10-min tick
  send_with_retry — Telegram send with exponential backoff
"""

import asyncio
import json
import logging
from datetime import datetime, time, timezone

import httpx
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from alerts import (
    format_afternoon_pulse,
    format_confirmation_alert,
    format_dispatch_response,
    format_drop_detected_alert,
    format_dsm_timeout_alert,
    format_eod_summary,
    format_morning_message,
    format_settlement_audit_alert,
    format_status,
)
from config import (
    BACKTEST_STARTING_BANK,
    CITIES,
    POLL_END_HOUR_EST,
    POLL_START_HOUR_LOCAL,
    TELEGRAM_RETRY_DELAYS,
)
from backtest.backtest_logger import BACKTEST_DIR, record_day
from kalshi import KalshiClient
from state import DailyState, StateManager
from weather import (
    ConfidenceLevel,
    SettlementAuditor,
    fetch_awc_tgroup,
    fetch_cli,
    fetch_forecast,
    fetch_hrrr_ceiling,
    fetch_metar,
    fetch_timeseries,
)

logger = logging.getLogger(__name__)

EST = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# Telegram send with retry
# ---------------------------------------------------------------------------

async def send_with_retry(bot, chat_id: str, text: str) -> bool:
    """
    Send a Telegram message.  Retries with exponential backoff on failure.
    Returns True only on confirmed success.
    Never raises.
    """
    for attempt, delay in enumerate(TELEGRAM_RETRY_DELAYS, start=1):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            if attempt > 1:
                logger.info("Telegram send succeeded on attempt %d", attempt)
            return True
        except Exception as exc:
            logger.warning(
                "Telegram send failed (attempt %d/%d): %s",
                attempt, len(TELEGRAM_RETRY_DELAYS), exc,
            )
            if attempt < len(TELEGRAM_RETRY_DELAYS):
                await asyncio.sleep(delay)

    logger.error(
        "Telegram send failed after %d attempts — will retry next poll cycle",
        len(TELEGRAM_RETRY_DELAYS),
    )
    return False


# ---------------------------------------------------------------------------
# Poll window guard
# ---------------------------------------------------------------------------

def _in_poll_window(config) -> bool:
    """
    Returns True if *now* is within the city's poll window:
      From: POLL_START_HOUR_LOCAL local time
      To:   POLL_END_HOUR_EST    EST

    Uses hour-based comparison to avoid the midnight bug where
    00:00 < 22:00 on the same calendar date would incorrectly
    allow polls after midnight EDT.
    """
    city_tz = pytz.timezone(config.tz)
    now_local = datetime.now(city_tz)
    now_est = datetime.now(EST)

    return (now_local.hour >= POLL_START_HOUR_LOCAL
            and POLL_START_HOUR_LOCAL <= now_est.hour < POLL_END_HOUR_EST)


def _required_drop_confirms(hour_local: int) -> int:
    """
    Number of consecutive poll cycles a drop must hold before the Triple-Lock opens.
    Scales with time of day — early afternoon drops are more likely to be head-fakes.

      Noon – 2 PM : 3 polls (~30 min sustained)
      2 PM – 4 PM : 2 polls (~20 min sustained)
      After 4 PM  : 1 poll  (fire immediately — drop at 4:30 PM is almost always real)
    """
    if hour_local < 14:
        return 3
    elif hour_local < 16:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Core poll cycle — runs per-city every 10 minutes
# ---------------------------------------------------------------------------

async def run_poll_cycle(
    bot,
    chat_id: str,
    state_manager: StateManager,
    kalshi_client: KalshiClient,
    station: str,
) -> None:
    """
    Full 8-step state machine for one city.  All steps are individually
    try/except wrapped — a failure in one step does not abort the rest.
    """
    config = CITIES[station]
    state = state_manager.get(station)
    city_tz = pytz.timezone(config.tz)

    # ── Pre-step: Prune stale readings from prior days ────────────────
    # When state is reloaded from JSON after a date rollover, metar_readings
    # may contain yesterday's data.  Filter to today's local date only.
    today_local = datetime.now(city_tz).date()
    before_count = len(state.metar_readings)
    state.metar_readings = [
        (dt, temp) for dt, temp in state.metar_readings
        if dt.astimezone(city_tz).date() == today_local
    ]
    if len(state.metar_readings) < before_count:
        pruned = before_count - len(state.metar_readings)
        logger.info("[%s] Pruned %d stale METAR readings from prior day", station, pruned)
        # Recompute suspected_high — the old peak may have been from yesterday
        if state.suspected_high_time and state.suspected_high_time.astimezone(city_tz).date() != today_local:
            if state.metar_readings:
                max_reading = max(state.metar_readings, key=lambda x: x[1])
                state.suspected_high = max_reading[1]
                state.suspected_high_time = max_reading[0]
            else:
                state.suspected_high = None
                state.suspected_high_time = None
            state.drop_detected = False
            state.drop_temp = None
            state.drop_time = None
            state.drop_alert_fired = False
            logger.info("[%s] Reset suspected high after pruning stale data", station)

    async with httpx.AsyncClient() as client:

        # ── Step 1: Fetch METAR ────────────────────────────────────────────
        try:
            # Fetch up to 24 hours so we capture the full day's readings,
            # not just the last 3 hours (which would miss the afternoon peak
            # if polled in the evening or early next morning).
            readings, err = await fetch_metar(client, station, hours=24)
            if err:
                state.log_error("METAR", err)
            today_local = datetime.now(city_tz).date()
            for r in readings:
                # Only accumulate readings from today's local date so we don't
                # bleed yesterday's temperatures into the suspected-high calc.
                obs_local_date = r.obs_time.astimezone(city_tz).date()
                if obs_local_date != today_local:
                    continue
                # Deduplicate by obs_time
                existing_times = {dt for dt, _ in state.metar_readings}
                if r.obs_time not in existing_times:
                    state.metar_readings.append((r.obs_time, r.temp_f))
        except Exception as exc:
            state.log_error("METAR", f"Unexpected error: {exc}")

        if not state.metar_readings:
            logger.info("[%s] No METAR readings yet — skipping cycle", station)
            state_manager.save(station)
            return

        # ── Step 2: Update suspected high + drop recovery reset ────────────
        max_reading = max(state.metar_readings, key=lambda x: x[1])
        new_high = max_reading[1]
        old_suspected = state.suspected_high  # capture before update

        if state.suspected_high is None or new_high > state.suspected_high:
            state.suspected_high = new_high
            state.suspected_high_time = max_reading[0]
            logger.info("[%s] Suspected high updated: %.0f°F", station, new_high)

            # Recovery reset: if the temp climbed back above a previous suspected high
            # and the drop alert hasn't fired yet, the earlier drop was a head-fake.
            # Reset drop state so we can detect the real drop later.
            if (
                old_suspected is not None
                and state.drop_detected
                and not state.drop_alert_fired
            ):
                logger.info(
                    "[%s] Drop RESET: temp %.0f°F recovered above suspected %.0f°F — head-fake",
                    station, new_high, old_suspected,
                )
                state.drop_detected = False
                state.drop_persist_count = 0
                state.drop_temp = None
                state.drop_time = None

        # ── Step 3: Drop detection + per-city afternoon alert ──────────────
        # Only detect drops after noon local — overnight temperature fluctuations
        # (e.g. 61°F at 1 AM → 59°F at 6 AM) are not meaningful daily peaks.
        now_local_hour = datetime.now(city_tz).hour
        if (
            not state.drop_detected
            and state.suspected_high_time is not None
            and now_local_hour >= POLL_START_HOUR_LOCAL
        ):
            readings_after_peak = [
                (dt, temp)
                for dt, temp in state.metar_readings
                if dt > state.suspected_high_time and temp < state.suspected_high
            ]
            if readings_after_peak:
                latest = max(readings_after_peak, key=lambda x: x[0])
                state.drop_detected = True
                state.drop_temp = latest[1]
                state.drop_time = latest[0]
                logger.info(
                    "[%s] Drop detected: %.0f°F at %s",
                    station, latest[1], latest[0],
                )

        # ── Triple-Lock Validation Gate ──────────────────────────────────
        # The bot is forbidden from firing a drop alert unless all 3 locks pass.
        # suspected_high continues updating in the background regardless.
        if state.drop_detected and not state.drop_alert_fired:
            # Increment persistence counter each poll the drop holds
            state.drop_persist_count += 1
            required = _required_drop_confirms(now_local_hour)
            if state.drop_persist_count < required:
                # Late-start bypass: if the suspected peak was 90+ minutes ago when we
                # first detect the drop, the bot was offline during the real drop event.
                # Real-time elapsed already proves the drop is sustained — skip poll wait.
                peak_age_min = 0.0
                if state.suspected_high_time:
                    peak_age_min = (
                        datetime.now(timezone.utc) - state.suspected_high_time
                    ).total_seconds() / 60
                if peak_age_min >= 90:
                    state.drop_persist_count = required  # fast-forward
                    logger.info(
                        "[%s] Drop persist BYPASS: peak was %.0f min ago (late start) — "
                        "fast-forwarded persist count to %d",
                        station, peak_age_min, required,
                    )
                else:
                    logger.info(
                        "[%s] Drop persist gate: count=%d, required=%d at hour=%d — holding",
                        station, state.drop_persist_count, required, now_local_hour,
                    )

        if state.drop_detected and not state.drop_alert_fired and \
                state.drop_persist_count >= _required_drop_confirms(now_local_hour):

            # LOCK 1: Physics Check (NWS Model Ceiling)
            # Two-sided window: suspected_high must be within 5°F below OR 2°F above
            # the forecast ceiling.  The upper bound catches spurious spikes that
            # overshoot the model — a real peak rarely exceeds the forecast by >2°F.
            lock1_pass = False
            try:
                ceiling, ceil_err = await fetch_hrrr_ceiling(
                    client, config.lat, config.lon,
                )
                if ceil_err:
                    state.log_error("Lock1_HRRR", ceil_err)
                elif ceiling is not None:
                    state.morning_model_high = ceiling
                    lower = ceiling - 5
                    upper = ceiling + 2
                    if lower <= state.suspected_high <= upper:
                        lock1_pass = True
                        logger.info(
                            "[%s] Lock1 PASS: suspected %.0f°F within window [%.0f–%.0f°F]",
                            station, state.suspected_high, lower, upper,
                        )
                    elif state.suspected_high > upper:
                        logger.info(
                            "[%s] Lock1 FAIL: suspected %.0f°F > ceiling %.0f°F + 2 — likely spurious spike",
                            station, state.suspected_high, ceiling,
                        )
                    else:
                        logger.info(
                            "[%s] Lock1 FAIL: suspected %.0f°F < ceiling %.0f°F - 5 — too cold, likely noise",
                            station, state.suspected_high, ceiling,
                        )
            except Exception as exc:
                state.log_error("Lock1_HRRR", f"Unexpected error: {exc}")

            # LOCK 2: NWS Observations Cross-check
            # Suspected high must be within 3°F of the NWS Obs API max for today.
            # Uses a different pipeline from Lock 1 (NWS api.weather.gov via fetch_timeseries).
            # FAIL-OPEN: if NWS Obs API is unavailable, treat as PASS so an outage
            # never permanently silences the bot.
            lock2_pass = False
            try:
                ts_readings, ts_err = await fetch_timeseries(
                    client, station, config.tz, limit=100
                )
                if ts_err:
                    logger.info(
                        "[%s] Lock2 BYPASS (fail-open): NWS Obs unavailable — %s",
                        station, ts_err,
                    )
                    lock2_pass = True
                elif ts_readings:
                    obs_high = max(t for _, t in ts_readings)
                    state.wethr_high = obs_high
                    if abs(state.suspected_high - obs_high) <= config.lock2_tolerance_f:
                        lock2_pass = True
                        logger.info(
                            "[%s] Lock2 PASS: suspected %.0f°F ≈ NWS obs %.0f°F (diff=%.0f, tol=%.0f)",
                            station, state.suspected_high, obs_high,
                            abs(state.suspected_high - obs_high), config.lock2_tolerance_f,
                        )
                    else:
                        logger.info(
                            "[%s] Lock2 FAIL: suspected %.0f°F vs NWS obs %.0f°F — mismatch "
                            "(diff=%.0f > tol=%.0f)",
                            station, state.suspected_high, obs_high,
                            abs(state.suspected_high - obs_high), config.lock2_tolerance_f,
                        )
                else:
                    logger.info("[%s] Lock2 BYPASS (fail-open): NWS Obs returned no readings", station)
                    lock2_pass = True
            except Exception as exc:
                logger.info("[%s] Lock2 BYPASS (fail-open): NWS Obs exception — %s", station, exc)
                lock2_pass = True

            # LOCK 3: Solar/Time Filter (already enforced by noon guard above,
            # but double-check here — no auto-confirm before noon local)
            lock3_pass = now_local_hour >= POLL_START_HOUR_LOCAL

            if lock1_pass and lock2_pass and lock3_pass:
                state.triple_lock_passed = True
                msg = format_drop_detected_alert(state, config)
                sent = await send_with_retry(bot, chat_id, msg)
                if sent:
                    state.drop_alert_fired = True
                    logger.info("[%s] Triple-Lock PASSED — drop alert sent (HIGH-LOCK confidence)", station)
            else:
                locks = f"L1={'PASS' if lock1_pass else 'FAIL'} L2={'PASS' if lock2_pass else 'FAIL'} L3={'PASS' if lock3_pass else 'FAIL'}"
                logger.info("[%s] Triple-Lock NOT passed (%s) — holding alert", station, locks)

        # ── Step 4: Time Series cross-check (informational) ────────────────
        if state.suspected_high and not state.dsm_confirmed:
            try:
                ts_readings, ts_err = await fetch_timeseries(
                    client, station, config.tz, limit=100
                )
                if ts_err:
                    state.log_error("TimeSeries", ts_err)
                elif ts_readings:
                    ts_max = max(t for _, t in ts_readings)
                    diff = abs(ts_max - state.suspected_high)
                    if diff > 3:
                        state.log_error(
                            "TimeSeries",
                            f"Peak mismatch: METAR shows {state.suspected_high:.0f}°F "
                            f"but NWS Obs API shows {ts_max:.0f}°F (diff={diff:.0f}°F)",
                        )
            except Exception as exc:
                state.log_error("TimeSeries", f"Unexpected error: {exc}")

        # ── Step 4.5: Settlement Audit (T-Group via AWC) ──────────────────
        # Runs once per day, right after the drop alert fires.
        # Not a gate — fail-open on any error.
        # HIGH   → early bracket prediction alert (actionable for MIA/ORD)
        # WARNING → rounding trap alert (wait for CLI before trading, all cities)
        # FAIL_OPEN → silent, standard flow continues
        if (
            state.drop_alert_fired
            and not state.dsm_confirmed
            and state.settlement_confidence is None   # only run once
        ):
            try:
                tg_max_c, tg_err = await fetch_awc_tgroup(client, station, hours=12)
                if tg_err:
                    logger.info("[%s] SettlementAudit FAIL_OPEN: %s", station, tg_err)
                    state.settlement_confidence = ConfidenceLevel.FAIL_OPEN.value
                else:
                    confidence, predicted_f, drift_f = SettlementAuditor.audit(
                        state.suspected_high, tg_max_c, config.tgroup_bias
                    )
                    state.predicted_settlement_f = predicted_f
                    state.settlement_confidence = confidence.value

                    if confidence == ConfidenceLevel.HIGH:
                        logger.info(
                            "[%s] SettlementAudit HIGH: predicted %.0f°F, drift=%.2f°F",
                            station, predicted_f, drift_f,
                        )
                    elif confidence == ConfidenceLevel.CAUTION:
                        logger.info(
                            "[%s] SettlementAudit CAUTION — Rounding Edge: "
                            "suspected %.0f°F vs predicted %.0f°F (drift=%.2f°F)",
                            station, state.suspected_high, predicted_f, drift_f,
                        )
                    else:  # WARNING
                        logger.warning(
                            "[%s] SettlementAudit WARNING — Rounding Trap: "
                            "suspected %.0f°F vs predicted %.0f°F (drift=%.2f°F) — wait for CLI",
                            station, state.suspected_high, predicted_f, drift_f,
                        )

                    # Kalshi price fetch — runs for ALL confidence levels.
                    # For HIGH: populates early bracket for display in the alert.
                    # For CAUTION/WARNING: price is recorded to price_history only (backtest).
                    early_ticker = None
                    early_bracket_low = None
                    early_bracket_high = None
                    early_price = None
                    try:
                        # Use predicted_f for HIGH (T-Group says bracket X), suspected_high
                        # for CAUTION/WARNING (METAR says bracket Y, T-Group disagrees).
                        lookup_temp = predicted_f if confidence == ConfidenceLevel.HIGH else state.suspected_high
                        markets, mkt_err = await kalshi_client.fetch_weather_markets(
                            client,
                            config.display_name,
                            series_candidates=config.kalshi_series_candidates,
                            target_date=datetime.now(city_tz).date(),
                        )
                        if not mkt_err and markets:
                            match = kalshi_client.find_bracket_for_temp(markets, lookup_temp)
                            if match:
                                spot_price = KalshiClient.extract_yes_ask(match)
                                # Always record to price_history for backtest
                                if spot_price is not None:
                                    state.price_history.append([
                                        datetime.utcnow().isoformat(), spot_price, "settlement_audit"
                                    ])
                                # Only expose bracket in the alert for HIGH confidence
                                if confidence == ConfidenceLevel.HIGH:
                                    bracket = match.get("parsed_bracket")
                                    early_ticker = match.get("ticker") or match.get("id")
                                    early_price = spot_price
                                    if bracket:
                                        early_bracket_low = bracket[0]
                                        early_bracket_high = bracket[1]
                                    logger.info(
                                        "[%s] Early bracket found: %s @ %.2f",
                                        station, early_ticker, spot_price or 0,
                                    )
                    except Exception as exc:
                        logger.info("[%s] Early bracket lookup failed (non-fatal): %s", station, exc)

                    msg = format_settlement_audit_alert(
                        state, config,
                        early_ticker=early_ticker,
                        early_bracket_low=early_bracket_low,
                        early_bracket_high=early_bracket_high,
                        early_price=early_price,
                        timestamp_str=datetime.now(city_tz).strftime("%#I:%M %p %Z"),
                    )
                    await send_with_retry(bot, chat_id, msg)

            except Exception as exc:
                logger.info("[%s] SettlementAudit exception (fail-open): %s", station, exc)
                state.settlement_confidence = ConfidenceLevel.FAIL_OPEN.value

        # ── Step 5: Official confirmation via CLI ─────────────────────────
        # DSM products are not available via NWS API for any of our cities.
        # CLI (Climate Report) is the primary and only confirmation source.
        # CLI is typically issued around 7-8 PM local time with today's max.
        # IMPORTANT: Don't attempt CLI confirmation before 5 PM local —
        # early-morning CLIs contain preliminary data (overnight max only,
        # not the actual daily high).
        CLI_MIN_HOUR_LOCAL = 17  # 5 PM local
        if (
            state.drop_detected
            and not state.dsm_confirmed
            and now_local_hour >= CLI_MIN_HOUR_LOCAL
        ):
            expected_date = datetime.now(city_tz).date()

            try:
                cli, cli_err = await fetch_cli(client, config.office)
                if cli_err:
                    state.log_error("CLI_confirm", cli_err)
                elif cli is not None and cli.report_date == expected_date:
                    if abs(cli.yesterday_high_f - state.suspected_high) <= 1:
                        state.dsm_confirmed = True
                        state.dsm_max_temp = cli.yesterday_high_f
                        state.dsm_issued_time = None
                        logger.info(
                            "[%s] CLI confirmed max %.0f°F (METAR suspected %.0f°F)",
                            station, cli.yesterday_high_f, state.suspected_high,
                        )
                    else:
                        state.dsm_hold_count += 1
                        logger.info(
                            "[%s] CLI shows %.0f°F, expected %.0f°F — hold #%d",
                            station, cli.yesterday_high_f, state.suspected_high,
                            state.dsm_hold_count,
                        )
                else:
                    logger.info(
                        "[%s] CLI not yet issued for today (report_date=%s, expected=%s)",
                        station, getattr(cli, "report_date", None), expected_date,
                    )
            except Exception as exc:
                state.log_error("CLI_confirm", f"Unexpected error: {exc}")

        # ── Step 6: Kalshi bracket lookup ─────────────────────────────────
        if state.dsm_confirmed and state.kalshi_ticker is None:
            try:
                markets, mkt_err = await kalshi_client.fetch_weather_markets(
                    client,
                    config.display_name,
                    series_candidates=config.kalshi_series_candidates,
                    target_date=datetime.now(city_tz).date(),
                )
                if mkt_err:
                    state.log_error("Kalshi", mkt_err)
                elif markets:
                    match = kalshi_client.find_bracket_for_temp(
                        markets, state.dsm_max_temp  # Use CLI-confirmed temp, not METAR
                    )
                    if match:
                        bracket = match.get("parsed_bracket")
                        state.kalshi_ticker = match.get("ticker") or match.get("id")
                        state.kalshi_price = KalshiClient.extract_yes_ask(match)
                        state.kalshi_close_time = KalshiClient.extract_close_time(match)
                        if bracket:
                            state.kalshi_bracket_low = bracket[0]
                            state.kalshi_bracket_high = bracket[1]
                        # Record confirmation price to price_history for backtest
                        if state.kalshi_price is not None:
                            state.price_history.append([
                                datetime.utcnow().isoformat(), state.kalshi_price, "confirmation"
                            ])
                        logger.info(
                            "[%s] Kalshi bracket found: %s @ %s",
                            station, state.kalshi_ticker, state.kalshi_price,
                        )
                    else:
                        logger.info(
                            "[%s] No matching Kalshi bracket for %.0f°F (CLI confirmed)",
                            station, state.dsm_max_temp,
                        )
            except Exception as exc:
                state.log_error("Kalshi", f"Unexpected error: {exc}")

        # ── Step 7: Fire confirmation alert ───────────────────────────────
        if state.dsm_confirmed and not state.alert_fired:
            msg = format_confirmation_alert(state, config, hold_count=state.dsm_hold_count)
            sent = await send_with_retry(bot, chat_id, msg)
            if sent:
                state.alert_fired = True
                logger.info("[%s] Confirmation alert sent", station)
            else:
                state.log_error("Telegram", "Failed to send confirmation alert — will retry")

        # ── Step 8: DSM timeout check ──────────────────────────────────────
        now_local_time = datetime.now(city_tz).time()
        if (
            now_local_time >= config.dsm_timeout_local
            and state.drop_detected
            and not state.dsm_confirmed
            and not state.dsm_timeout_fired
        ):
            msg = format_dsm_timeout_alert(state, config)
            sent = await send_with_retry(bot, chat_id, msg)
            if sent:
                state.dsm_timeout_fired = True
                logger.info("[%s] DSM timeout alert sent", station)

    # Persist state to disk after every cycle
    state_manager.save(station)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def morning_job(bot, chat_id: str, state_manager: StateManager) -> None:
    """8:00 AM EST — Fetch forecasts + yesterday's CLI for all cities, send morning brief."""
    logger.info("Running morning_job")
    cli_results = {}
    cli_errors = {}
    forecast_results = {}
    forecast_errors = {}
    today = datetime.now(EST).date()

    async with httpx.AsyncClient() as client:
        for station, config in CITIES.items():
            # Fetch yesterday's CLI
            try:
                cli, err = await fetch_cli(client, config.office)
                cli_results[station] = cli
                cli_errors[station] = err
            except Exception as exc:
                cli_results[station] = None
                cli_errors[station] = str(exc)

            # Fetch today's NWS forecast high and store as model ceiling
            try:
                fc, fc_err = await fetch_forecast(client, config.lat, config.lon)
                forecast_results[station] = fc
                forecast_errors[station] = fc_err
                if fc is not None:
                    state = state_manager.get(station)
                    state.morning_model_high = fc.high_f
                    state_manager.save(station)
                    logger.info("[%s] Morning model ceiling set: %.0f°F", station, fc.high_f)
            except Exception as exc:
                forecast_results[station] = None
                forecast_errors[station] = str(exc)

    msg = format_morning_message(
        cli_results, cli_errors,
        forecast_results, forecast_errors,
        CITIES, today,
    )
    await send_with_retry(bot, chat_id, msg)


async def eod_job(bot, chat_id: str, state_manager: StateManager) -> None:
    """10:00 PM EST — Record backtest snapshots then send end-of-day summary."""
    logger.info("Running eod_job")
    today = datetime.now(EST).date()
    states = {s: state_manager.get(s) for s in CITIES}

    # Write backtest records BEFORE sending summary (fail-open — never blocks EOD)
    backtest_ok = []
    backtest_fail = []
    for station in CITIES:
        try:
            record_day(station, states[station], CITIES[station])
            backtest_ok.append(station)
        except Exception as exc:
            logger.warning("Backtest record failed for %s (non-fatal): %s", station, exc)
            backtest_fail.append((station, str(exc)))

    # Backtest confirmation message
    if backtest_fail:
        fail_lines = "\n".join(f"  {s}: {err}" for s, err in backtest_fail)
        bt_msg = (
            f"⚠️  BACKTEST LOG — {today.isoformat()}\n"
            f"Saved: {', '.join(backtest_ok) if backtest_ok else 'none'}\n"
            f"FAILED:\n{fail_lines}"
        )
    else:
        bt_msg = (
            f"✅  BACKTEST LOG — {today.isoformat()}\n"
            f"All 3 cities recorded: {', '.join(backtest_ok)}"
        )
    await send_with_retry(bot, chat_id, bt_msg)

    # Compute per-city P&L and running balance from freshly-written backtest records
    city_pnl: dict = {}
    running_balance: float | None = None
    try:
        today_str = today.isoformat()
        all_records = []
        if BACKTEST_DIR.exists():
            for p in sorted(BACKTEST_DIR.glob("*.json")):
                try:
                    all_records.append(json.loads(p.read_text()))
                except Exception:
                    pass

        total_pnl = sum(
            r["economics"].get("actual_pnl", 0.0)
            for r in all_records
            if r["economics"].get("trade_outcome") not in (None, "no_trade", "pending")
        )
        running_balance = round(BACKTEST_STARTING_BANK + total_pnl, 2)

        for rec in all_records:
            if rec["meta"]["date"] == today_str:
                s = rec["meta"]["station"]
                ep = rec["economics"].get("price_at_settlement_audit") or rec["economics"].get("price_at_confirmation")
                city_pnl[s] = {
                    "trade_outcome":    rec["economics"].get("trade_outcome", "no_trade"),
                    "actual_pnl":       rec["economics"].get("actual_pnl", 0.0),
                    "contracts":        rec["economics"].get("contracts", 0),
                    "entry_price_cents": round(ep * 100) if ep is not None else None,
                }
    except Exception as exc:
        logger.warning("Could not compute P&L for EOD summary: %s", exc)

    msg = format_eod_summary(states, CITIES, today, city_pnl=city_pnl, running_balance=running_balance)
    await send_with_retry(bot, chat_id, msg)


async def afternoon_pulse_job(bot, chat_id: str, state_manager: StateManager) -> None:
    """2:00 PM EST — Send a single mid-afternoon check-in covering all three cities."""
    logger.info("Running afternoon_pulse_job")
    states = {s: state_manager.get(s) for s in CITIES}
    msg = format_afternoon_pulse(states, CITIES)
    await send_with_retry(bot, chat_id, msg)


async def midnight_job(state_manager: StateManager) -> None:
    """12:00 AM EST — Reset all city states for the new trading day."""
    logger.info("Running midnight_job — resetting state for new day")
    state_manager.reset_all()


async def poll_city_job(
    bot,
    chat_id: str,
    state_manager: StateManager,
    kalshi_client: KalshiClient,
    station: str,
) -> None:
    """
    Runs every 10 minutes per city.
    Silently skips if outside the poll window.
    Wraps run_poll_cycle with top-level exception guard.
    """
    config = CITIES[station]
    if not _in_poll_window(config):
        return

    logger.info("[%s] Poll cycle starting", station)
    try:
        await run_poll_cycle(bot, chat_id, state_manager, kalshi_client, station)
    except Exception as exc:
        logger.exception("[%s] Unexpected error in poll cycle: %s", station, exc)
        state = state_manager.get(station)
        state.log_error("Scheduler", f"Poll cycle crashed: {exc}")
        state_manager.save(station)


# ---------------------------------------------------------------------------
# Manual /dispatch — run cross-reference for all 3 cities right now
# ---------------------------------------------------------------------------

async def run_dispatch(
    bot,
    chat_id: str,
    state_manager: StateManager,
    kalshi_client: KalshiClient,
) -> None:
    """
    Trigger a full poll cycle for all cities regardless of poll window,
    then send a combined dispatch message.
    """
    logger.info("Manual /dispatch triggered")

    # Run poll cycles for all cities
    for station in CITIES:
        try:
            await run_poll_cycle(bot, chat_id, state_manager, kalshi_client, station)
        except Exception as exc:
            logger.exception("[%s] Error during dispatch poll: %s", station, exc)

    # Build and send the combined dispatch message
    states = {s: state_manager.get(s) for s in CITIES}
    metar_summaries = {
        s: state_manager.get(s).metar_readings[-3:]
        for s in CITIES
    }
    dsm_statuses = {}
    lock_statuses = {}
    for s, state in states.items():
        if state.dsm_confirmed:
            dsm_statuses[s] = f"Confirmed — {state.dsm_max_temp:.0f}°F"
        elif state.dsm_timeout_fired:
            dsm_statuses[s] = "Timeout — never confirmed"
        elif state.drop_detected:
            dsm_statuses[s] = "Pending — awaiting DSM update"
        else:
            dsm_statuses[s] = "Not checked — no drop detected yet"

        # Build Triple-Lock status string
        if state.triple_lock_passed:
            lock_statuses[s] = "ALL LOCKS PASSED ✅"
        else:
            parts = []
            cfg = CITIES[s]
            if state.morning_model_high is not None:
                if state.suspected_high and state.suspected_high >= (state.morning_model_high - 5):
                    parts.append(f"L1:PASS (model={state.morning_model_high:.0f}°F)")
                else:
                    parts.append(f"L1:FAIL (model={state.morning_model_high:.0f}°F, suspected={state.suspected_high or 0:.0f}°F)")
            else:
                parts.append("L1:PENDING")
            if state.wethr_high is not None:
                if state.suspected_high and abs(state.suspected_high - state.wethr_high) <= 3:
                    parts.append(f"L2:PASS (wethr={state.wethr_high:.0f}°F)")
                else:
                    parts.append(f"L2:FAIL (wethr={state.wethr_high:.0f}°F)")
            else:
                parts.append("L2:PENDING")
            city_tz = pytz.timezone(cfg.tz)
            hour_now = datetime.now(city_tz).hour
            parts.append(f"L3:{'PASS' if hour_now >= POLL_START_HOUR_LOCAL else 'FAIL'} ({hour_now}:00 local)")
            lock_statuses[s] = " | ".join(parts)

    msg = format_dispatch_response(
        states, CITIES, metar_summaries, dsm_statuses, lock_statuses
    )

    # Prepend manual override warning if any locks haven't passed
    any_unconfirmed = any(
        s.drop_detected and not s.triple_lock_passed
        for s in states.values()
    )
    if any_unconfirmed:
        warning = (
            "⚠️ MANUAL OVERRIDE: Data not yet confirmed by Model/Sync.\n"
            "Triple-Lock validation has NOT passed for one or more cities.\n"
            "Verify data independently before trading.\n\n"
        )
        msg = warning + msg

    await send_with_retry(bot, chat_id, msg)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def setup_scheduler(
    bot,
    chat_id: str,
    state_manager: StateManager,
    kalshi_client: KalshiClient,
) -> AsyncIOScheduler:
    """
    Create and configure the AsyncIOScheduler.
    Call scheduler.start() after this, then keep the event loop running.
    """
    scheduler = AsyncIOScheduler(timezone="America/New_York")

    # Morning brief — 8:00 AM EST
    scheduler.add_job(
        morning_job,
        CronTrigger(hour=8, minute=0, timezone="America/New_York"),
        args=[bot, chat_id, state_manager],
        id="morning_brief",
        replace_existing=True,
    )

    # End-of-day summary — 10:00 PM EST
    scheduler.add_job(
        eod_job,
        CronTrigger(hour=22, minute=0, timezone="America/New_York"),
        args=[bot, chat_id, state_manager],
        id="eod_summary",
        replace_existing=True,
    )

    # Afternoon pulse — 2:00 PM EST
    scheduler.add_job(
        afternoon_pulse_job,
        CronTrigger(hour=14, minute=0, timezone="America/New_York"),
        args=[bot, chat_id, state_manager],
        id="afternoon_pulse",
        replace_existing=True,
    )

    # Midnight reset — 12:00 AM EST
    scheduler.add_job(
        midnight_job,
        CronTrigger(hour=0, minute=0, timezone="America/New_York"),
        args=[state_manager],
        id="midnight_reset",
        replace_existing=True,
    )

    # Per-city polling — every 10 minutes
    # KMIA starts at 12 PM EST; KAUS/KMDW start at 1 PM EST (= 12 PM CST)
    # The poll window guard inside each job handles the actual gating.

    scheduler.add_job(
        poll_city_job,
        IntervalTrigger(minutes=10, timezone="America/New_York"),
        args=[bot, chat_id, state_manager, kalshi_client, "KMIA"],
        id="poll_kmia",
        replace_existing=True,
    )

    scheduler.add_job(
        poll_city_job,
        IntervalTrigger(minutes=10, timezone="America/New_York"),
        args=[bot, chat_id, state_manager, kalshi_client, "KAUS"],
        id="poll_kaus",
        replace_existing=True,
    )

    scheduler.add_job(
        poll_city_job,
        IntervalTrigger(minutes=10, timezone="America/New_York"),
        args=[bot, chat_id, state_manager, kalshi_client, "KMDW"],
        id="poll_kmdw",
        replace_existing=True,
    )

    return scheduler
