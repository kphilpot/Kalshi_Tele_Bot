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
import logging
from datetime import datetime, time, timezone

import httpx
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from alerts import (
    format_confirmation_alert,
    format_dispatch_response,
    format_drop_detected_alert,
    format_dsm_timeout_alert,
    format_eod_summary,
    format_morning_message,
    format_status,
)
from config import (
    CITIES,
    POLL_END_HOUR_EST,
    POLL_START_HOUR_LOCAL,
    TELEGRAM_RETRY_DELAYS,
)
from kalshi import KalshiClient
from state import DailyState, StateManager
from weather import fetch_cli, fetch_forecast, fetch_metar, fetch_timeseries

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

        # ── Step 2: Update suspected high ──────────────────────────────────
        max_reading = max(state.metar_readings, key=lambda x: x[1])
        new_high = max_reading[1]

        if state.suspected_high is None or new_high > state.suspected_high:
            state.suspected_high = new_high
            state.suspected_high_time = max_reading[0]
            logger.info("[%s] Suspected high updated: %.0f°F", station, new_high)

        # ── Step 3: Drop detection + per-city afternoon alert ──────────────
        if not state.drop_detected and state.suspected_high_time is not None:
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

        # Send "peak detected" alert once per city when drop is first detected
        if state.drop_detected and not state.drop_alert_fired:
            msg = format_drop_detected_alert(state, config)
            sent = await send_with_retry(bot, chat_id, msg)
            if sent:
                state.drop_alert_fired = True
                logger.info("[%s] Drop-detected alert sent", station)

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

        # ── Step 5: Official confirmation via CLI ─────────────────────────
        # DSM products are not available via NWS API for any of our cities.
        # CLI (Climate Report) is the primary and only confirmation source.
        # CLI is typically issued around 7-8 PM local time with today's max.
        if state.drop_detected and not state.dsm_confirmed:
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

            # Fetch today's NWS forecast high
            try:
                fc, fc_err = await fetch_forecast(client, config.lat, config.lon)
                forecast_results[station] = fc
                forecast_errors[station] = fc_err
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
    """10:00 PM EST — Send end-of-day summary."""
    logger.info("Running eod_job")
    today = datetime.now(EST).date()
    states = {s: state_manager.get(s) for s in CITIES}
    msg = format_eod_summary(states, CITIES, today)
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
    for s, state in states.items():
        if state.dsm_confirmed:
            dsm_statuses[s] = f"Confirmed — {state.dsm_max_temp:.0f}°F"
        elif state.dsm_timeout_fired:
            dsm_statuses[s] = "Timeout — never confirmed"
        elif state.drop_detected:
            dsm_statuses[s] = "Pending — awaiting DSM update"
        else:
            dsm_statuses[s] = "Not checked — no drop detected yet"

    msg = format_dispatch_response(states, CITIES, metar_summaries, dsm_statuses)
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
