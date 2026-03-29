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
