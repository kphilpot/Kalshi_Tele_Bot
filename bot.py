"""
bot.py — Entry point for the Kalshi Weather Telegram Bot.

Initialises the Telegram Application, registers command handlers,
starts the APScheduler, and runs the polling loop.

Commands:
  /start    — Confirm the bot is alive, show monitoring window summary
  /ping     — Quick alive check with current time and METAR data status
  /dispatch — Manual trigger: full cross-reference for all 3 cities right now
  /status   — Compact current state for all 3 cities
  /reset    — Reset state for one or all cities (admin/debug use)
"""

import asyncio
import logging
import sys
from datetime import datetime

import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from alerts import format_status
from config import CITIES, get_telegram_chat_id, get_telegram_token
from kalshi import KalshiClient
from scheduler import run_dispatch, setup_scheduler
from state import StateManager

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

EST = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Shared objects (initialised in main, injected into handlers via bot_data)
# ---------------------------------------------------------------------------

def _state_manager(context: ContextTypes.DEFAULT_TYPE) -> StateManager:
    return context.application.bot_data["state_manager"]


def _kalshi_client(context: ContextTypes.DEFAULT_TYPE) -> KalshiClient:
    return context.application.bot_data["kalshi_client"]


def _chat_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data["chat_id"]


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with bot status and monitoring schedule overview."""
    _now = datetime.now(EST)
    now_est = f"{_now.hour % 12 or 12}:{_now.strftime('%M %p')} EST"
    lines = [
        "Kalshi Weather Bot is running.",
        f"Current time: {now_est}",
        "",
        "Monitoring:",
        "  KAUS (Austin)   — polls 12 PM–10 PM CST",
        "  KMIA (Miami)    — polls 12 PM–10 PM EST",
        "  KMDW (Chicago)  — polls 12 PM–10 PM CST",
        "",
        "Commands:",
        "  /ping     — Quick alive check",
        "  /dispatch — Manual cross-reference for all cities",
        "  /status   — Current state summary",
        "  /reset    — Reset state (e.g. /reset KAUS or /reset all)",
    ]
    await update.message.reply_text("\n".join(lines))


async def dispatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /dispatch — Run full cross-reference for all 3 cities immediately.
    Always covers all cities; no arguments.
    """
    await update.message.reply_text(
        "Running manual dispatch for all cities... (this may take 30–60 seconds)"
    )
    sm = _state_manager(context)
    kc = _kalshi_client(context)
    chat_id = _chat_id(context)

    try:
        await run_dispatch(context.bot, chat_id, sm, kc)
    except Exception as exc:
        logger.exception("Error during /dispatch: %s", exc)
        await update.message.reply_text(f"Dispatch encountered an error: {exc}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Compact current state for all 3 cities."""
    sm = _state_manager(context)
    states = {s: sm.get(s) for s in CITIES}
    msg = format_status(states, CITIES)
    await update.message.reply_text(msg)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ping — Confirm the bot is alive and responding."""
    _now = datetime.now(EST)
    now_str = _now.strftime("%#I:%M %p EST")
    sm = _state_manager(context)
    # Count cities with active polling state today
    active = sum(
        1 for s in CITIES if sm.get(s).metar_readings
    )
    await update.message.reply_text(
        f"Bot is running.\n"
        f"Time: {now_str}\n"
        f"Cities with METAR data today: {active}/3"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /reset [STATION|all]
    Reset state for one city (e.g. /reset KAUS) or all cities (/reset all).
    Admin/debug use only.
    """
    sm = _state_manager(context)
    args = context.args or []
    target = args[0].upper() if args else "all"

    if target == "ALL":
        sm.reset_all()
        await update.message.reply_text("State reset for all cities. JSON files deleted.")
        return

    if target in CITIES:
        sm.reset_one(target)
        await update.message.reply_text(f"State reset for {target}. JSON file deleted.")
    else:
        await update.message.reply_text(
            f"Unknown station '{target}'. Valid options: {', '.join(CITIES.keys())} or all"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    telegram_token = get_telegram_token()
    chat_id = get_telegram_chat_id()

    state_manager = StateManager()
    kalshi_client = KalshiClient()

    # Build the Telegram application
    app = Application.builder().token(telegram_token).build()

    # Store shared objects so handlers can access them
    app.bot_data["state_manager"] = state_manager
    app.bot_data["kalshi_client"] = kalshi_client
    app.bot_data["chat_id"] = chat_id

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("dispatch", dispatch_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))

    # Setup scheduler (does not start it yet)
    scheduler = setup_scheduler(app.bot, chat_id, state_manager, kalshi_client)

    async def post_init(application: Application) -> None:
        scheduler.start()
        logger.info("Scheduler started — bot is live")

    async def post_shutdown(application: Application) -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        state_manager.save_all()
        logger.info("Scheduler stopped — state saved")

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info("Starting Kalshi Weather Bot — polling for updates")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
