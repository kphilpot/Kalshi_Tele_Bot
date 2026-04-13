"""
Code Snippets for Integrating Paper Trading into scheduler.py

Copy these code blocks into the appropriate locations in your scheduler.py file.
DO NOT modify any other code — just insert these snippets.
"""

# ============================================================================
# SNIPPET 1: IMPORTS (Add to top of scheduler.py after existing imports)
# ============================================================================

"""
Place this at the top of scheduler.py, after existing imports:

    from paper_trading.framework import PaperTradingFramework
"""

# ============================================================================
# SNIPPET 2: INITIALIZE FRAMEWORK (Add during bot startup)
# ============================================================================

"""
Place this during initialization (e.g., in main() function):

OPTION 1: Without Telegram alerts (silent mode):
    # Initialize paper trading framework
    # Runs multiple strategies in parallel without affecting real bot
    paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2"])
    logger.info("Paper trading framework initialized")

OPTION 2: With Telegram alerts (RECOMMENDED):
    # Initialize paper trading framework with Telegram notifications
    paper_trader = PaperTradingFramework(
        enabled_strategies=["TIER1", "TIER2"],
        telegram_bot=bot,  # Your existing Telegram bot instance
        chat_id=PAPER_TRADING_CHAT_ID  # Chat ID for alerts
    )
    logger.info("Paper trading framework initialized")

Example context:
    def main():
        ... existing setup code ...

        # Initialize paper trading framework
        paper_trader = PaperTradingFramework(
            enabled_strategies=["TIER1", "TIER2"],
            telegram_bot=bot,
            chat_id=PAPER_TRADING_CHAT_ID
        )
        logger.info("Paper trading framework initialized")

        ... rest of startup ...
"""

# ============================================================================
# SNIPPET 3: FEED DATA TO PAPER TRADER (Every poll cycle)
# ============================================================================

"""
Place this INSIDE run_poll_cycle(), after METAR and predictions are collected.

Add it right after you've set:
- state.predicted_settlement_f (T-Group prediction)
- state.hrrr_max_temp_f (HRRR ceiling)
- state.metar_readings (METAR history)

And BEFORE any real trading decisions.

Code to insert:

    # =========== PAPER TRADING (Non-intrusive shadow strategies) ===========
    # Feed data to all enabled strategies (does not affect real bot)
    try:
        paper_decisions = paper_trader.process_poll_cycle(
            timestamp=datetime.utcnow(),
            station=station,
            city=config.display_name,
            metar_history=state.metar_readings[-3:] if len(state.metar_readings) >= 3 else state.metar_readings,
            tgroup_prediction=state.predicted_settlement_f if state.predicted_settlement_f else 85.0,
            hrrr_ceiling=state.hrrr_max_temp_f if state.hrrr_max_temp_f else 90.0,
        )
        # Paper trading results are logged automatically
    except Exception as e:
        logger.exception(f"[Paper Trading] Error: {e}")
        # Errors in paper trading do NOT affect real bot
    # =========================================================================

Example context in run_poll_cycle():

    async def run_poll_cycle(...):
        ... existing code to collect METAR ...

        # Get predictions
        state.predicted_settlement_f = await get_tgroup_prediction(...)
        state.hrrr_max_temp_f = await get_hrrr_ceiling(...)

        # =========== PAPER TRADING ===========
        try:
            paper_decisions = paper_trader.process_poll_cycle(
                timestamp=datetime.utcnow(),
                station=station,
                city=config.display_name,
                metar_history=state.metar_readings[-3:] if len(state.metar_readings) >= 3 else state.metar_readings,
                tgroup_prediction=state.predicted_settlement_f if state.predicted_settlement_f else 85.0,
                hrrr_ceiling=state.hrrr_max_temp_f if state.hrrr_max_temp_f else 90.0,
            )
        except Exception as e:
            logger.exception(f"[Paper Trading] Error: {e}")
        # ===================================

        ... rest of existing code (triple lock, alerts, etc.) ...
"""

# ============================================================================
# SNIPPET 4: END-OF-DAY PROCESSING (After CLI arrives)
# ============================================================================

"""
Place this in your end-of-day function (eod_job() or similar).

Call this AFTER the CLI has confirmed the settlement (usually 7-9 PM).

Code to insert:

    # =========== PAPER TRADING END-OF-DAY ===========
    # Back-fill actual settlement results for all strategies
    for station, config in CITY_CONFIGS.items():
        state = states[station] if station in states else None

        if state and state.cli_max_temp_f is not None:
            paper_trader.end_of_day(
                date=str(datetime.now().date()),
                station=station,
                actual_cli_high=state.cli_max_temp_f
            )
    # ================================================

Example context in eod_job():

    async def eod_job():
        logger.info("Running end-of-day job")

        ... existing EOD code ...

        # =========== PAPER TRADING EOD ===========
        for station, config in CITY_CONFIGS.items():
            state = states[station] if station in states else None

            if state and state.cli_max_temp_f is not None:
                paper_trader.end_of_day(
                    date=str(datetime.now().date()),
                    station=station,
                    actual_cli_high=state.cli_max_temp_f
                )
        # =========================================

        ... rest of EOD code ...
"""

# ============================================================================
# COMPLETE EXAMPLE: How to integrate all snippets
# ============================================================================

"""
COMPLETE scheduler.py integration example (pseudocode):

=== TOP OF FILE ===
import logging
from datetime import datetime
# ... other imports ...

from paper_trading.framework import PaperTradingFramework  # SNIPPET 1

=== MAIN / STARTUP ===
async def main():
    logger = logging.getLogger(__name__)

    # ... existing startup code ...

    # Initialize paper trading framework with Telegram alerts
    paper_trader = PaperTradingFramework(  # SNIPPET 2
        enabled_strategies=["TIER1", "TIER2"],
        telegram_bot=bot,
        chat_id=PAPER_TRADING_CHAT_ID
    )
    logger.info("Paper trading framework initialized")

    # ... rest of startup ...

    # Start scheduler
    scheduler.start()

=== INSIDE run_poll_cycle() ===
async def run_poll_cycle(bot, states, configs):
    for station, config in configs.items():
        state = states[station]

        # ... existing METAR collection code ...

        # Get predictions
        state.predicted_settlement_f = 86.0  # From T-Group
        state.hrrr_max_temp_f = 88.0         # From HRRR

        # SNIPPET 3: Feed data to paper trader
        try:
            paper_decisions = paper_trader.process_poll_cycle(
                timestamp=datetime.utcnow(),
                station=station,
                city=config.display_name,
                metar_history=state.metar_readings[-3:] if len(state.metar_readings) >= 3 else state.metar_readings,
                tgroup_prediction=state.predicted_settlement_f if state.predicted_settlement_f else 85.0,
                hrrr_ceiling=state.hrrr_max_temp_f if state.hrrr_max_temp_f else 90.0,
            )
        except Exception as e:
            logger.exception(f"[Paper Trading] Error: {e}")

        # ... rest of existing poll cycle (unchanged) ...

=== INSIDE eod_job() ===
async def eod_job(bot, states, configs):
    logger.info("Running end-of-day job")

    # ... existing EOD code ...

    # SNIPPET 4: Back-fill paper trading results
    for station, config in configs.items():
        state = states[station]

        if state.cli_max_temp_f is not None:
            paper_trader.end_of_day(
                date=str(datetime.now().date()),
                station=station,
                actual_cli_high=state.cli_max_temp_f
            )

    # ... rest of EOD code ...
"""

# ============================================================================
# VIEWING RESULTS (After integration)
# ============================================================================

"""
After running the bot for a few days, view paper trading results:

Option 1: View latest day's summary
    python -m paper_trading.reporters.daily_viewer

Option 2: View specific date
    python -m paper_trading.reporters.daily_viewer summary 2026-04-13

Option 3: View comparative analysis
    python -m paper_trading.reporters.daily_viewer comparative 2026-04-13

Option 4: List all available dates
    python -m paper_trading.reporters.daily_viewer list

Output example:
    ====================================================================================================
    PAPER TRADING SUMMARY: 2026-04-13
    ====================================================================================================

    TIER 1: Settlement Audit (Conservative):
      Trades fired:        1
      Trades won:          1
      Trades lost:         0
      Win rate:            100.0%
      Total P&L:           $0.28
      Avg entry price:     $0.72

    TIER 2: Rate-of-Change Peak Detection:
      Trades fired:        2
      Trades won:          2
      Trades lost:         0
      Win rate:            100.0%
      Total P&L:           $0.66
      Avg entry price:     $0.35
"""

# ============================================================================
# IMPORTANT NOTES
# ============================================================================

"""
1. Paper trading runs SILENTLY in the background
   - No Telegram alerts
   - No user interaction
   - No effect on real bot

2. All errors are caught and logged
   - Paper trading errors do NOT affect real bot
   - Check scheduler logs if issues arise

3. Logs are created automatically
   - paper_trading_logs/YYYY-MM-DD/ directory created daily
   - TIER1_STATION.jsonl, TIER2_STATION.jsonl, etc.
   - SUMMARY_all_strategies.json for comparison

4. No hardcoding required
   - Framework auto-configures based on actual data
   - Handles missing METAR readings gracefully
   - Defaults to reasonable values if predictions missing

5. Adding new strategies is simple
   - Create paper_trading/strategies/tiernew_name.py
   - Inherit from BaseStrategy
   - Implement 3 methods
   - Enable in framework: ["TIER1", "TIER2", "TIERNEW"]
"""

print(__doc__)
