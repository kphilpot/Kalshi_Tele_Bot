# Paper Trading System

## What Is This?

A **multi-strategy shadow trading system** that runs in parallel with your main bot. It evaluates the same METAR data through different strategies (TIER 1, TIER 2, etc.) and compares their performance without affecting your real bot.

Think of it as a **research lab attached to your trading bot** — you can test new strategies and see how they would have performed, all while the real bot operates normally.

## Why?

Your original question: **"Should we trade at 1 PM (TIER 2) or 3 PM (TIER 1)?"**

Instead of theorizing, this system will show you empirically:
- How many trades would TIER 2 have fired?
- Would they have been correct?
- How does the P&L compare to TIER 1?
- Is the extra complexity worth 60+ minutes earlier entry?

## Quick Start

### 1. System is Ready
All code is already written. No coding required.

### 2. Integrate with Main Bot
Edit `scheduler.py` and add 2-4 lines of code (with optional Telegram alerts). See [PAPER_TRADING_INTEGRATION.md](PAPER_TRADING_INTEGRATION.md) for exact location.

### 3. Run Bot Normally
The paper trading framework runs in the background:
- **Silent mode** — No alerts, just logging
- **With Telegram** — Receive alerts when trades fire and daily summaries (RECOMMENDED)

### 4. After 3-5 Days, Check Results
```bash
python -m paper_trading.reporters.daily_viewer summary 2026-04-13
```

## Telegram Alerts (Optional)

You can optionally enable Telegram notifications to receive alerts in real-time:

```python
paper_trader = PaperTradingFramework(
    enabled_strategies=["TIER1", "TIER2"],
    telegram_bot=bot,           # Your Telegram bot instance
    chat_id=PAPER_TRADING_CHAT_ID
)
```

**You'll receive:**
- ✅ Initialization alert when framework starts
- 📊 Alert when each paper trade fires (confidence, entry price, expected value)
- 📈 Daily summary after EOD (all strategy P&L + winner)

All alerts clearly note these are **paper trades** and your real bot is unaffected.

---

## What Gets Logged?

### Per Poll Cycle (every 10 minutes)
- Current METAR reading
- Confidence score for each strategy
- Decision: TRADE or NO_TRADE
- Assumed entry price
- Expected value if traded

### End of Day
- Actual settlement (from CLI)
- Win/Loss for each trade
- P&L for each trade
- Daily summary comparing all strategies

### Sample Output
```
TIER 1: Settlement Audit
  Trades fired: 1
  Win rate: 100%
  Total P&L: $0.28

TIER 2: Rate-of-Change
  Trades fired: 2
  Win rate: 100%
  Total P&L: $0.66

Winner: TIER 2 (+$0.38 advantage)
```

## Current Strategies

### TIER 1: Settlement Audit (Conservative)
- **Entry time:** 3-4 PM
- **Logic:** Trade only when settlement audit is ready
- **Accuracy:** Very high (proven)
- **Use as:** Baseline for comparison

### TIER 2: Rate-of-Change Peak Detection
- **Entry time:** 1-2 PM (if signals align)
- **Logic:** Detect when temperature acceleration slowing (peak imminent)
- **Accuracy:** ~90% (estimated)
- **Use as:** Test if early entry is worth the risk

### Future: TIER 3 (Placeholder)
Add your own adaptive strategies when ready.

## Adding New Strategies

1. Create `paper_trading/strategies/tier3_yourname.py`
2. Inherit from `BaseStrategy`
3. Implement 3 methods:
   - `calculate_confidence_score()` — Your scoring logic
   - `should_trade()` — Decision threshold
   - `get_decision_details()` — What to log

4. Enable in scheduler:
```python
paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2", "TIER3"])
```

See [PAPER_TRADING_INTEGRATION.md](PAPER_TRADING_INTEGRATION.md) for full example.

## File Structure

```
paper_trading/
├── framework.py              ← Main engine (feeds data to strategies)
├── config.py                 ← Global settings
├── logger.py                 ← Logging system
├── strategies/
│   ├── base_strategy.py      ← Abstract base (all strategies inherit)
│   ├── tier1_settlement_audit.py
│   └── tier2_rate_of_change.py
└── reporters/
    └── daily_viewer.py       ← Viewing tool

paper_trading_logs/
└── 2026-04-13/
    ├── TIER1_KAUS.jsonl      ← Decisions per poll
    ├── TIER2_KAUS.jsonl
    ├── SUMMARY_all_strategies.json
    └── COMPARATIVE_REPORT.json
```

## Integration Checklist

- [ ] Read [PAPER_TRADING_INTEGRATION.md](PAPER_TRADING_INTEGRATION.md)
- [ ] Add imports to `scheduler.py` (1 line)
- [ ] Initialize framework (2 lines)
- [ ] Call `process_poll_cycle()` in main polling loop (5 lines)
- [ ] Call `end_of_day()` after CLI arrives (3 lines)
- [ ] Run bot normally (no changes to real trading)
- [ ] After 3-5 days, view results:
  ```bash
  python -m paper_trading.reporters.daily_viewer
  ```

## Example: What Data Looks Like

### Decision Log (TIER2_KAUS.jsonl)
```json
{
  "timestamp_utc": "2026-04-13T18:00:00Z",
  "hour": 14,
  "station": "KAUS",
  "confidence_score": 75,
  "decision": "TRADE",
  "assumed_entry_price": 0.35,
  "expected_value": 0.27,
  "details": {
    "v1_first_hour_fph": 3.5,
    "v2_second_hour_fph": 1.5,
    "acceleration_fph": -2.0,
    "rate_decline_pct": 57.1,
    "interpretation": "Peak imminent (rate declining >50%)"
  },
  "ground_truth": {
    "actual_cli_high": 85.0,
    "entry_price": 0.35,
    "won": true,
    "pnl": 0.65
  }
}
```

### Daily Summary
```json
{
  "date": "2026-04-13",
  "strategies": {
    "TIER1": {
      "trades_fired": 1,
      "trades_won": 1,
      "win_rate": "100.0%",
      "total_pnl": 0.28
    },
    "TIER2": {
      "trades_fired": 2,
      "trades_won": 2,
      "win_rate": "100.0%",
      "total_pnl": 0.66
    }
  }
}
```

## Viewing Results

```bash
# View latest summary
python -m paper_trading.reporters.daily_viewer

# View specific date
python -m paper_trading.reporters.daily_viewer summary 2026-04-13

# View comparison
python -m paper_trading.reporters.daily_viewer comparative 2026-04-13

# List all dates with data
python -m paper_trading.reporters.daily_viewer list
```

## Key Points

1. **Zero impact** — Paper trading runs in isolation, errors are caught
2. **Real data** — Uses actual METAR, T-Group, HRRR from your main bot
3. **Automatic** — No manual trades, no alerts, just logging
4. **Extensible** — Add strategies anytime without touching framework
5. **Empirical** — Answer "TIER 2 vs TIER 1?" with real data, not theory

## Troubleshooting

**No logs created?**
- Check `paper_trading_logs/` directory was created
- Verify `process_poll_cycle()` is called
- Check scheduler logs for errors

**Logs empty?**
- METAR data may not be passed correctly
- `metar_history` needs at least 1-2 readings
- Check `tgroup_prediction` and `hrrr_ceiling` are not None

**Want to clear old logs?**
- Delete `paper_trading_logs/` directory
- It will be recreated automatically

## Next Steps

1. Follow [PAPER_TRADING_INTEGRATION.md](PAPER_TRADING_INTEGRATION.md) to integrate
2. Run bot normally for 3-5 days
3. Review results: `python -m paper_trading.reporters.daily_viewer`
4. Decide: Is TIER 2 worth the complexity? Does it actually win more?
5. If yes: Consider TIER 3 or automation
6. If no: Stick with TIER 1 (proven, simple, 3-4 PM entry)

## Questions?

See [PAPER_TRADING_INTEGRATION.md](PAPER_TRADING_INTEGRATION.md) for detailed integration instructions and code examples.
