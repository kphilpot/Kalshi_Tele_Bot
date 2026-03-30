# Session Log - Kalshi_Tele_Bot
**Session Start:** 2026-03-28

## Work Completed This Session
- Replaced wethr.net Lock 2 (permanently gated) with NWS Observations API (fetch_timeseries)
- Added per-city lock2_tolerance_f: Austin tightened to 2.0°F (was 3.0°F for all cities)
- Implemented T-Group Settlement Auditor (ConfidenceLevel: HIGH/CAUTION/WARNING/FAIL_OPEN)
- Added fetch_awc_tgroup() pulling Aviation Weather Center METAR JSON
- Added drop persistence gate (_required_drop_confirms) — time-scaled by local hour
- Added head-fake recovery reset in Step 2 of poll cycle
- Lock 1 upgraded to two-sided window: ceiling -5°F to ceiling +2°F (catches spurious spikes)
- Added CAUTION confidence level; Austin always escalates CAUTION → WARNING
- Added format_settlement_audit_alert() in alerts.py
- Updated format_confirmation_alert() to CLI Scorecard framing with T-Group match check
- Added 2 PM EST afternoon pulse (format_afternoon_pulse, afternoon_pulse_job)
- Added backtest infrastructure: backtest/backtest_logger.py, backtest/analyze.py
- Added price_history field to DailyState for tracking Kalshi prices at key events
- Added per-city max_entry_price_cents to CityConfig (Miami 88c, Chicago 85c, Austin 80c)
- Replaced global PRICE_FLAG_THRESHOLD with per-city ceiling in all _trade_flags callers
- Added backtest confirmation Telegram message in eod_job (success/failure per city)
- Fixed 3 bugs: _trade_flags single-flag text, EOD summary using METAR vs CLI, dead CAUTION code

## Blockers/Questions
- Need 2-3 weeks of live data before calibrating price ceilings further
- Austin 90% accuracy target requires real price data to validate

---

# Session Log - Kalshi_Tele_Bot
**Session Start:** 2026-03-29 19:00

## Work Completed This Session
- Changed price "TRADE NOT ADVISED" threshold from per-city ceilings (80/85/88¢) to universal 97¢ (PRICE_FLAG_THRESHOLD in config.py)
- Removed per-city max_entry_price_cents / 100 override from all 3 _trade_flags() call sites in alerts.py
- Fixed Austin HIGH confidence logic: Austin now gets early entry pass on HIGH (same as MIA/ORD), not "Wait for CLI"; CAUTION/WARNING still escalate to WARNING DO NOT TRADE
- Added timestamp_str parameter to format_settlement_audit_alert(); all three confidence branches (HIGH/CAUTION/WARNING) now show signal time in the alert header
- scheduler.py passes local city time via datetime.now(city_tz).strftime("%#I:%M %p %Z") to settlement audit alert

## Blockers/Questions
- All three cities priced at 100¢ today before any early entry window opened (Chicago Lock 1 failed due to 11°F forecast miss; Miami WARNING; Austin HIGH but market already saturated)
- Need live data on whether Austin HIGH days have a price gap between settlement audit and market close
