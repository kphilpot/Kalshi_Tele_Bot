# Paper Trading System Integration Guide

## Overview

The paper trading system runs in parallel with the main bot without affecting it. Every 10 minutes during polling, the paper trading framework evaluates the same METAR data through multiple strategies and logs decisions.

## Directory Structure

```
Kalshi Tele Bot/
├── scheduler.py              (unchanged - main bot)
├── paper_trading/            (NEW - shadow strategies)
│   ├── __init__.py
│   ├── config.py
│   ├── logger.py
│   ├── framework.py
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base_strategy.py
│   │   ├── tier1_settlement_audit.py
│   │   ├── tier2_rate_of_change.py
│   └── reporters/
│       ├── __init__.py
│       └── daily_viewer.py
│
├── paper_trading_logs/       (NEW - output logs)
│   ├── 2026-04-13/
│   │   ├── TIER1_KAUS.jsonl
│   │   ├── TIER1_KMDW.jsonl
│   │   ├── TIER1_KMIA.jsonl
│   │   ├── TIER2_KAUS.jsonl
│   │   ├── TIER2_KMDW.jsonl
│   │   ├── TIER2_KMIA.jsonl
│   │   ├── SUMMARY_all_strategies.json
│   │   └── COMPARATIVE_REPORT.json
│   └── 2026-04-14/ (next day)
```

## Integration Steps

### 1. Import at Top of scheduler.py

Add these imports at the very top of `scheduler.py` (after existing imports):

```python
# Paper trading framework
from paper_trading.framework import PaperTradingFramework
```

### 2. Initialize Framework (One-time, on startup)

In the main bot initialization (around where you set up logging), add:

**WITHOUT Telegram alerts:**
```python
# Initialize paper trading framework
# Runs TIER1 and TIER2 strategies in parallel with real bot
paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2"])
logger.info("Paper trading framework initialized")
```

**WITH Telegram alerts (RECOMMENDED):**
```python
# Initialize paper trading framework with Telegram notifications
# Sends alerts when paper trades fire and daily summaries
paper_trader = PaperTradingFramework(
    enabled_strategies=["TIER1", "TIER2"],
    telegram_bot=bot,  # Your existing Telegram bot instance
    chat_id=PAPER_TRADING_CHAT_ID  # Telegram chat ID for alerts
)
logger.info("Paper trading framework initialized")
```

The framework will automatically send:
1. ✅ Initialization alert when framework starts
2. 📊 Alert when each paper trade fires (strategy, confidence, price)
3. 📈 Daily summary after EOD processing (all strategy P&L + winner)

### 3. Feed Data to Paper Trader (Every Poll Cycle)

Inside `run_poll_cycle()`, after you've collected METAR data and calculated predictions, add this code:

```python
# =========== PAPER TRADING (Non-intrusive shadow strategies) ===========
# Feed data to all enabled strategies (does not affect real bot)
try:
    paper_decisions = paper_trader.process_poll_cycle(
        timestamp=datetime.utcnow(),
        station=station,
        city=config.display_name,
        metar_history=state.metar_readings[-3:] if len(state.metar_readings) >= 3 else state.metar_readings,
        tgroup_prediction=state.predicted_settlement_f,
        hrrr_ceiling=state.hrrr_max_temp_f if state.hrrr_max_temp_f else 100.0,
    )
    # Paper trading results are logged automatically, no further action needed
except Exception as e:
    logger.exception(f"Paper trading error: {e}")
    # Do NOT let paper trading errors affect the real bot
# =========================================================================
```

**Where exactly in run_poll_cycle()?** Add it right after you've set:
- `state.predicted_settlement_f` (T-Group prediction)
- `state.hrrr_max_temp_f` (HRRR ceiling)
- `state.metar_readings` (METAR history)

And BEFORE any real trading decisions that might affect the user.

### 4. End-of-Day Processing (After CLI Arrives)

In `eod_job()` or similar end-of-day function, add:

```python
# =========== PAPER TRADING EOD ===========
# Back-fill actual settlement results for all strategies
for station, config in CITY_CONFIGS.items():
    state = get_daily_state(station)  # However you get the state
    
    if state.cli_max_temp_f is not None:
        paper_trader.end_of_day(
            date=str(datetime.now().date()),
            station=station,
            actual_cli_high=state.cli_max_temp_f
        )
# ==========================================
```

## Telegram Alerts (Optional but Recommended)

The paper trading framework can send Telegram notifications to keep you informed:

### What You'll Receive

1. **Framework Started Alert** (on initialization)
   ```
   ✅ PAPER TRADING FRAMEWORK STARTED
   
   Strategies enabled: TIER1, TIER2
   
   The shadow bot is now running in the background.
   You will receive updates when trades are fired and daily summaries.
   
   Your real bot is unaffected.
   ```

2. **Trade Fired Alert** (every time a paper trade fires)
   ```
   📊 PAPER TRADE FIRED: TIER2
   
   Strategy: Rate-of-Change Peak Detection
   Station: KAUS (Austin)
   Time: 14:00
   Confidence: 75/100
   Entry Price: $0.35
   Expected Value: $0.27
   
   This is a paper trade (shadow bot). Your real bot is unaffected.
   ```

3. **Daily Summary Alert** (after EOD processing)
   ```
   📈 PAPER TRADING DAILY SUMMARY: 2026-04-13
   
   TIER 1: Settlement Audit (Conservative)
     Trades: 1 | Win rate: 100.0%
     P&L: $0.28 | Avg entry: $0.72
   
   TIER 2: Rate-of-Change Peak Detection
     Trades: 2 | Win rate: 100.0%
     P&L: $0.66 | Avg entry: $0.35
   
   COMPARISON
     Winner: TIER2
     Difference: $0.38
   ```

### How to Enable

1. Make sure you have your Telegram `bot` object and `PAPER_TRADING_CHAT_ID` in scheduler.py
2. Pass them to the framework on initialization:
   ```python
   paper_trader = PaperTradingFramework(
       enabled_strategies=["TIER1", "TIER2"],
       telegram_bot=bot,
       chat_id=PAPER_TRADING_CHAT_ID
   )
   ```

### How to Disable

Simply don't pass `telegram_bot` and `chat_id` — the framework will run silently:
```python
paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2"])
```

---

## Viewing Results

### Option 1: View Latest Summary

```bash
cd "path/to/Kalshi Tele Bot"
python -m paper_trading.reporters.daily_viewer
```

This will show today's (or yesterday's) summary.

### Option 2: View Specific Date

```bash
python -m paper_trading.reporters.daily_viewer summary 2026-04-13
```

### Option 3: View Comparative Report

```bash
python -m paper_trading.reporters.daily_viewer comparative 2026-04-13
```

### Option 4: List All Dates

```bash
python -m paper_trading.reporters.daily_viewer list
```

## Sample Output

After running for one day, you'll see:

```
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

====================================================================================================

COMPARATIVE ANALYSIS: 2026-04-13
Best performer: TIER2
P&L: $0.66

TIER 1 vs TIER 2:
  TIER 1 P&L:  $0.28 (1 trades)
  TIER 2 P&L:  $0.66 (2 trades)
  Difference:  $0.38
  Winner:      TIER2
```

## Log Files Generated

For each day, you'll have:

### TIER1_KAUS.jsonl
One JSON object per line, one per poll cycle:
```json
{
  "timestamp_utc": "2026-04-13T17:00:00Z",
  "hour": 13,
  "station": "KAUS",
  "confidence_score": 0,
  "decision": "NO_TRADE",
  "assumed_entry_price": 0.35,
  "expected_value": 0.0,
  "ground_truth": null
}

{
  "timestamp_utc": "2026-04-13T20:00:00Z",
  "hour": 16,
  "station": "KAUS",
  "confidence_score": 95,
  "decision": "TRADE",
  "assumed_entry_price": 0.72,
  "expected_value": 0.2,
  "ground_truth": {
    "actual_cli_high": 85.0,
    "entry_price": 0.72,
    "won": true,
    "pnl": 0.28
  }
}
```

### TIER2_KAUS.jsonl
Similar format, but with TIER2-specific details:
```json
{
  "timestamp_utc": "2026-04-13T18:00:00Z",
  "hour": 14,
  "station": "KAUS",
  "confidence_score": 65,
  "decision": "NO_TRADE",
  "details": {
    "v1_first_hour_fph": 3.5,
    "v2_second_hour_fph": 1.5,
    "acceleration_fph": -2.0,
    "rate_decline_pct": 57.1,
    "gates": {"check_engine_light": "PASS", "small_numbers_gate": "PASS"}
  }
}
```

### SUMMARY_all_strategies.json
High-level comparison of all strategies for the day:
```json
{
  "date": "2026-04-13",
  "strategies": {
    "TIER1": {
      "strategy_id": "TIER1",
      "trades_fired": 1,
      "trades_won": 1,
      "win_rate": "100.0%",
      "total_pnl": 0.28
    },
    "TIER2": {
      "strategy_id": "TIER2",
      "trades_fired": 2,
      "trades_won": 2,
      "win_rate": "100.0%",
      "total_pnl": 0.66
    }
  }
}
```

## Adding Custom Strategies

To add a new strategy (e.g., TIER3):

1. Create `paper_trading/strategies/tier3_custom.py`
2. Inherit from `BaseStrategy`
3. Implement three methods:
   - `calculate_confidence_score()`
   - `should_trade()`
   - `get_decision_details()`

Example:
```python
from .base_strategy import BaseStrategy

class TIER3Custom(BaseStrategy):
    def __init__(self):
        super().__init__(
            strategy_name="TIER 3: Custom Strategy",
            strategy_id="TIER3"
        )
        self.confidence_threshold = 75
    
    def calculate_confidence_score(self, metar_history, tgroup, hrrr, current_time):
        # Your custom logic here
        return 75
    
    def should_trade(self, confidence_score):
        return confidence_score >= self.confidence_threshold
    
    def get_decision_details(self, metar_history, tgroup, hrrr):
        return {"custom": "details"}
```

Then enable it:
```python
paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2", "TIER3"])
```

## Key Points

1. **Zero impact on real bot** — Paper trading runs in parallel, all errors are caught
2. **Automatic logging** — Every decision is logged, zero manual work
3. **Easy comparison** — View which strategy performs better after a few days
4. **Extensible** — Add new strategies anytime without modifying framework
5. **Simple integration** — Just 2-3 lines added to scheduler.py

## Troubleshooting

### No logs created?
- Check that `paper_trading_logs/` directory exists (created automatically)
- Verify `paper_trader.process_poll_cycle()` is being called every poll cycle
- Check scheduler logs for errors

### Logs created but empty?
- Verify METAR data is being passed correctly
- Make sure `metar_history` has at least 1-2 readings
- Check that `tgroup_prediction` and `hrrr_ceiling` are not None

### Want to restart logging?
- Simply delete `paper_trading_logs/` directory
- It will be recreated automatically on next poll cycle

## Checking It's Working

After the first day of running:

```bash
# Check if logs were created
ls -la paper_trading_logs/2026-04-13/

# Should see:
# TIER1_KAUS.jsonl
# TIER1_KMDW.jsonl
# TIER1_KMIA.jsonl
# TIER2_KAUS.jsonl
# TIER2_KMDW.jsonl
# TIER2_KMIA.jsonl
# SUMMARY_all_strategies.json
# COMPARATIVE_REPORT.json

# View the summary
python -m paper_trading.reporters.daily_viewer summary 2026-04-13
```

Done! The paper trading framework is now running silently in the background.
