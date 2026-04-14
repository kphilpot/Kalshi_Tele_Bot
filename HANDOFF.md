# Kalshi Weather Telegram Bot — Handoff Prompt

**Last updated**: 2026-04-02
**Commit**: `b2fd7aa` + pending fixes (bracket_low serialization, morning markets log-only, requirements.txt)

---

## What This Bot Does

Monitors NWS temperature data for 3 US cities and sends Telegram alerts when it detects the daily high temperature, matches it to a Kalshi binary options bracket market, and recommends whether the trade is viable based on price.

**Cities monitored**: Austin (KAUS), Miami (KMIA), Chicago (KMDW)

**Daily cycle**: Morning brief → METAR polling → Peak detection → Triple-Lock validation → Settlement audit → CLI confirmation → Kalshi bracket lookup → Confirmation alert → EOD summary

---

## Architecture

### Files (7 Python files)

| File | Purpose |
|------|---------|
| `bot.py` | Entry point. Telegram app, 5 command handlers (`/start`, `/ping`, `/dispatch`, `/status`, `/reset`), scheduler wiring |
| `config.py` | City configs (station, office, tz, coords, Kalshi series, thresholds), URL constants, env accessors |
| `scheduler.py` | APScheduler: morning_job (8 AM), poll_city_job (every 10 min), afternoon_pulse (2 PM), eod_job (10 PM), midnight_job (12 AM). Contains the 8-step `run_poll_cycle` state machine |
| `state.py` | `DailyState` dataclass (per-city daily state) + `StateManager` (JSON persistence, midnight reset) |
| `weather.py` | All NWS/AWC data fetching: `fetch_metar`, `fetch_timeseries`, `fetch_cli`, `fetch_forecast`, `fetch_hrrr_ceiling`, `fetch_awc_tgroup`, `SettlementAuditor` |
| `kalshi.py` | Kalshi REST API: RSA-PSS per-request signing, market discovery (Tier1 series → Tier2 broad), bracket parsing (structured fields + title regex), bracket matching |
| `alerts.py` | Pure message formatting (no I/O). One function per notification type |
| `backtest/backtest_logger.py` | End-of-day JSON snapshot writer for paper-trading P&L tracking |

### Deployment
- Runs locally on Dell 3640 workstation (Windows 11), sleep/hibernate disabled
- Python process with `python-telegram-bot` polling loop
- State persisted to `state/` directory as JSON per-city per-day
- Backtest records in `backtest/data/YYYY-MM-DD_STATION.json`

### Environment Variables (.env)
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
KALSHI_API_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=...
```

---

## The 8-Step Poll Cycle (`run_poll_cycle`)

Runs every 10 minutes per city during poll window (noon local → 10 PM EST).

1. **Fetch METAR** — 24h lookback, deduplicate, filter to today's local date
2. **Update suspected high** — Track peak temp and time. Head-fake recovery: if a new higher reading appears after drop, reset drop state
3. **Drop detection** — After noon local, if current temp < suspected_high, mark drop. Persistence gate requires 1-3 consecutive confirms depending on hour
4. **Triple-Lock validation** — All 3 must pass before drop alert fires:
   - **Lock 1 (Physics)**: suspected_high within [model_ceiling - 5, model_ceiling + 2]
   - **Lock 2 (NWS Obs)**: |suspected_high - NWS_obs_high| ≤ tolerance (2-3°F per city)
   - **Lock 3 (Time)**: current hour ≥ noon local
5. **Step 4.5: Settlement Audit** — T-Group via AWC METAR (runs once at `drop_detected`, not gated on `drop_alert_fired`). Outputs HIGH/CAUTION/WARNING/FAIL_OPEN confidence. Fetches early Kalshi bracket + price
6. **Step 5: CLI Confirmation** — After 5 PM local, fetch NWS Climate Report. Confirms if |CLI_high - suspected_high| ≤ 1°F. Stores `cli_last_high_f` even on mismatch. Sends hold notification on mismatch (first + every 6th poll)
7. **Step 6: Kalshi bracket lookup** — After CLI confirms, find matching market using `confirmed_high < low` convention
8. **Step 6.5: Price threshold** — Track if price crosses $0.75
9. **Step 7: Confirmation alert** — Send to Telegram
10. **Step 8: DSM timeout** — At configured local time (8 PM), fire timeout alert
11. **Step 8.5: Force confirmation** — If timeout fired + CLI available + gap ≤ 2°F, force-confirm using CLI value

---

## CRITICAL: Kalshi Bracket Convention

**This is the #1 thing to get right. Getting this wrong = real money lost.**

Kalshi weather "between X and Y" markets are **not** standard intervals. The resolution rule per the official contract spec:

| Market Type | Example | Resolves YES if... |
|-------------|---------|-------------------|
| "between X and Y" (B-type) | B40.5 ("between 40 and 41") | temp **< X** (the floor) |
| "less than X" (T-type) | T74 ("below 74") | temp **≤ cap** |
| "greater than X" | "above 90" | temp **≥ floor** |

**For a confirmed high of T°F, the correct bracket has the smallest floor > T.**

Example: Chicago confirmed high = 41°F
- B40.5 (floor=40): `41 < 40` = False → skip
- B41.5 (floor=41): `41 < 41` = False → skip
- B42.5 (floor=42): `41 < 42` = True → **CORRECT** (matches "42-43" bracket)

**Code locations**:
- `kalshi.py:397` — `in_bracket = confirmed_high < low`
- `backtest_logger.py:120` — `bracket_correct = actual < lo`
- `alerts.py:_market_rule()` — display formatting

**Never change this to `<=` or `lo <= actual <= hi` — that was the April 1 bug that caused real losses.**

---

## Known Issues & Recent Fixes (April 2, 2026)

### Fixed
- **Bracket matching** — Changed from `lo <= temp <= hi` (wrong) to `temp < lo` (correct Kalshi convention)
- **AWC T-Group format** — Switched from `format=json` + `rawOb` field (broke April 1) to `format=raw` + line parsing
- **Settlement audit gate** — Changed from `drop_alert_fired` to `drop_detected` so audit runs even when triple-lock fails
- **CLI hold visibility** — Bot now sends Telegram notification when CLI/METAR diverge (was silently stuck)
- **Timeout force-confirmation** — After timeout, if CLI gap ≤ 2°F, auto-confirm
- **bracket_low -inf serialization** — Now sanitized for JSON (was crash bug on "less than" markets)
- **Morning Kalshi market fetch** — Bot logs all markets + resolution rules at 8 AM (internal only, not sent to user)
- **Missing cryptography dep** — Added to requirements.txt

### Known Remaining Issues
- `datetime.utcnow()` deprecated (Python 3.12+) — used in state.py, backtest_logger.py. Cosmetic, not breaking
- `DSMResult.issued_time` is always None — dead field, never populated
- `format_status` imported in scheduler.py but only used in bot.py — now cleaned up
- `_in_poll_window` has a redundant `POLL_START_HOUR_LOCAL <=` EST check — harmless but confusing
- `>` and `>=` title patterns treated identically in `parse_bracket_from_title` — minor, structured fields are preferred over title parsing

---

## Backtest System

- **Location**: `backtest/data/YYYY-MM-DD_STATION.json`
- **Trigger**: `eod_job` at 10 PM EST calls `record_day()` for each city
- **Data collected**: 15 records (Mar 28 – Apr 1, 3 cities)
- **Paper trading**: Starting bank $30, 10% risk per trade, compounding balance
- **Fields**: meta, setup, detection, triple_lock, settlement_audit, ground_truth, economics (P&L), alerts
- **Status**: ✅ Actively collecting. Need 10+ more days before implementing confidence-based early confirmation

---

## Notification Types (Telegram)

1. **Morning Brief** (8 AM EST) — Yesterday's CLI results, today's NWS forecast high per city
2. **Afternoon Check-in** (2 PM EST) — One-line status per city
3. **Drop Alert** (per-city, ~3-6 PM local) — Peak detected, Triple-Lock passed, settlement audit result
4. **Settlement Audit Alert** (per-city) — T-Group confidence (HIGH/CAUTION/WARNING), early bracket + price
5. **CLI Hold Notification** (per-city) — When CLI/METAR disagree beyond 1°F threshold
6. **Confirmation Alert** (per-city) — CLI confirmed, Kalshi bracket + price, trade viability
7. **Timeout Force-Confirm** (per-city) — If CLI and METAR haven't converged by timeout
8. **EOD Summary** (10 PM EST) — All 3 cities, P&L, backtest status

---

## Per-City Configuration

| Setting | Austin (KAUS) | Miami (KMIA) | Chicago (KMDW) |
|---------|---------------|--------------|-----------------|
| NWS Office | AUS | MIA | MDW |
| Timezone | America/Chicago | America/New_York | America/Chicago |
| T-Group bias | +0.15°F | 0.0°F | 0.0°F |
| Max entry price | 80¢ | 88¢ | 85¢ |
| Lock 2 tolerance | 2.0°F | 3.0°F | 3.0°F |
| DSM timeout | 8:00 PM local | 8:00 PM local | 8:00 PM local |
| Kalshi series | KXHIGHAUS | KXHIGHMIA | KXHIGHCHI |

---

## Data Sources

| Source | API | Used For |
|--------|-----|----------|
| AWC METAR | `aviationweather.gov/api/data/metar` (format=raw) | METAR temps + T-Group remarks |
| NWS Observations | `api.weather.gov/stations/{}/observations` | Lock 2 cross-check |
| NWS Forecast | `api.weather.gov/gridpoints/{}/{}/forecast` | Lock 1 model ceiling |
| NWS CLI Product | `forecast.weather.gov/product.php?product=CLI` | Official daily high confirmation |
| Kalshi Markets | `api.elections.kalshi.com/trade-api/v2/markets` | Bracket discovery + pricing |

---

## Brainstormed Improvements (Not Yet Implemented — Need More Data)

See `DEBUG_REPORT_and_CONFIRMATION_BRAINSTORM.md` for full analysis. Key ideas:
1. **Confidence-Based Early Confirmation** — needs 10+ days of METAR vs CLI accuracy data
2. **T-Group Threshold Shift** — use HIGH confidence as soft pre-confirmation
3. **NWS Obs Alignment** — NWS Obs updates 10x/day vs CLI once
4. **Earlier Timeout** — move from 8 PM to 7 PM local
5. **Model Lock 1 + Peak Momentum** — 30-min stasis = stable peak

**Current priority**: Collect 10 more days of backtest data. Don't force early confirmation without validated accuracy.
