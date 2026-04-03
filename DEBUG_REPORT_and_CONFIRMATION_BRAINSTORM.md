# Debug Report & Earlier Confirmation Brainstorm

## PART 1: FIXES VERIFIED ✅

### 1. Bracket Logic (Kalshi Chicago Rule)
**Confirmed match**: `kalshi.py:397` uses `confirmed_high < low`

Example with Chicago B40.5 (market says "YES if temp < 40°F"):
- `floor=40, cap=41`
- If confirmed_high=41: `41 < 40` = False → NOT selected ✅
- If confirmed_high=39: `39 < 40` = True → SELECTED ✅
- **CORRECT** — matches the rule exactly

### 2. Backtest Logging (bracket_correct)
**Confirmed**: `backtest_logger.py:120` uses `actual < lo` — mirrors bracket logic
- Historical records will now show correct win/loss

### 3. T-Group Early Run Issue
**Root cause identified**: Not really "early" — it runs at drop_detected, which happens ~4-5 PM on the backtest days.
- **Real problem**: AWC API `format=json` field rename on April 1 → silent FAIL_OPEN
- **Fixed**: Switched to `format=raw` (like fetch_metar)
- **Settlement audit gate**: Now uses `state.drop_detected` instead of `state.drop_alert_fired` ✅

### 4. Backtest Data Collection
**Status**: ✅ Confirmed active
- Files present: Mar 28–Apr 1, all 3 cities (15 records total)
- Data quality: Good — includes prices, timestamps, outcomes, economic details
- March 28: Bot offline (stub record, no METAR)
- March 29–31: Complete runs, all no_trade (prices = $1.00)
- April 1: COMPLETE DATA including bracket_correct, CLI confirmed, actual P&L

---

## PART 2: WEATHER.GOV CLIMATE PORTAL

**URL**: `https://www.weather.gov/wrh/Climate?wfo=lot`
- `lot` = Chicago NWS office (LOT = Lake Ontario)
- Shows past temperature records, daily highs/lows, CLI text
- **Valid?** YES — but it's a **human-readable text page**, not an API

**Current bot uses**:
1. NWS API (`api.weather.gov`) — structured, reliable
2. AWC API (`aviationweather.gov`) — METAR/T-Group
3. `weather.gov/Product` (for CLI/DSM text) — parsed via BeautifulSoup

**Should you use the Climate portal?** NO — it's the same data as the product page you're already scraping. The API is more reliable.

---

## PART 3: BACKTEST DATA ANALYSIS — EARLIER CONFIRMATION BRAINSTORM

### Current Confirmation Timeline
```
Detection → Drop → CLI (5 PM local) → Confirmation
                   ↑                      ↑
                   Earliest possible      Actual time
```

### What the Data Shows

| Date | City | Detection | CLI | Actual Confirmed | Time Gap | Note |
|------|------|-----------|-----|------------------|----------|------|
| 3/29 | CHI  | 6:53 PM   | ~8 PM | 11:58 PM (next day) | ~17 hours | Lock 1 failed (model=74, peak=63) |
| 3/30 | CHI  | 8:53 PM   | ~9 PM | 1:31 AM (next day) | ~5 hours | Lock 1 failed (model=74, peak=81) |
| 3/31 | CHI  | 5:14 AM   | ~8 PM | 5:06 PM same day | ~12 hours | Lock 1 passed (model=69, peak=70) |
| 4/1  | CHI  | 5:14 AM   | ~8 PM | 10:06 PM same day | ~17 hours | Lock 1 passed (model=40, peak=41) |

**Key observation**: March 31 was fastest (12 hours) because triple-lock passed early.

### Brainstorm: 6 Ways to Get Earlier Confirmation (Without Forcing)

#### **1. "Confidence-Based Early Confirmation" (⭐ BEST — need more data)**
**Idea**: Track accuracy of METAR peak vs final CLI across multiple days. Once you have 10+ days of history showing METAR peak = ±0.5°F of final CLI, reduce tolerance from 1°F to 0.5°F.

**Requirements**:
- 10+ days of backtest data (you have 5 good days now — need 5-10 more)
- Track per-city: Chicago vs Austin vs Miami might converge at different rates
- Only apply after model calibration (Lock 1) passes

**Expected gain**: 30-60 min earlier confirmation on "high confidence" days
**Risk**: Near-zero if you wait for enough data

**Data needed**: Daily records with confirmed CLI and METAR readings (you already collect this!)

---

#### **2. "T-Group Threshold Shift" (⭐ GOOD — can do now with current data)**
**Idea**: Currently T-Group outputs CAUTION/WARNING when drift > 0.5°F. Use the Settlement Auditor confidence as an early "soft confirmation" hint.

**Current state**:
- T-Group runs at drop_detected (~4-5 PM)
- If HIGH confidence → predicted_settlement_f is available early

**Proposed change**:
- If T-Group confidence = HIGH and drift < 0.3°F, and CLI matches predicted ±0.5°F when it arrives → fire confirmation alert earlier (don't wait for full 1°F CLI-METAR match)

**Requirements**:
- Analyze March 31 / April 1: did T-Group HIGH catch the right number before CLI?
- Need 3-5 more HIGH confidence days to validate

**Expected gain**: 1-3 hours (could confirm at 5:30 PM if T-Group HIGH + CLI arrives early and matches)
**Risk**: Low if you require T-Group HIGH + CLI validation

---

#### **3. "Model Lock 1 + Peak Momentum" (⭐ MEDIUM — need trend data)**
**Idea**: If Lock 1 passes (model ceiling is plausible) AND the peak hasn't moved >1°F in the last 30 min, soft-confirm at 6 PM local (1 hour before CLI typically arrives).

**Logic**:
- Peak at 5:55 PM = 82°F
- No update for 30 min (last reading at 5:25 PM also 82°F)
- Lock 1 passed
- → Soft-confirm to 82°F at 6 PM EST (before waiting for CLI)

**Requirements**:
- METAR readings frequency (you have this — every ~10 min from poll cycle)
- Lock 1 validation (you have this)

**Expected gain**: 1 hour
**Risk**: Peak could rise one more time after 30-min stasis. Mitigate: only apply if past 6 PM local (late afternoon)

---

#### **4. "NWS Observations API Alignment" (⭐ GOOD — can test now)**
**Idea**: You already fetch NWS Observations API (Lock 2). It updates more frequently than CLI (every 1-3 hours vs CLI once per day).

**Current**:
- CLI fetched at 5 PM local (once a day)
- NWS Obs (Lock 2) fetched every poll (every 10 min)

**Proposed**:
- If NWS Obs high matches METAR peak ±0.5°F AND Lock 1 passes by 6 PM → treat as "preliminary confirmation" and move to bracket lookup
- Validate against CLI when it arrives
- If CLI differs >1°F, backtrack and wait for resolution

**Requirements**:
- Historical NWS Obs data in backtest (you may not be storing this)
- Need to verify NWS Obs API reliability for daily highs

**Expected gain**: 1-2 hours
**Risk**: Moderate — NWS Obs can have a 1-2 hour lag. Validate with more data.

---

#### **5. "Timeout Fallback (Already Implemented) + Trigger Threshold" (⭐ EASY — use now)**
**Current state**: You added timeout force-confirmation if CLI and METAR gap ≤ 2°F at timeout

**Enhancement**:
- Move timeout from 9 PM → 7 PM local (2 hours earlier)
- Slightly looser tolerance at timeout: if gap ≤ 1.5°F (instead of 2°F), confirm using closest reading
- Rationale: by 7 PM, you have 2 hours of data post-peak; peak is stable

**Requirements**: None — use existing logic
**Expected gain**: 2 hours
**Risk**: Low — 1.5°F is still tight; if temp moves >1.5°F, you timeout without confirming

---

#### **6. "Pre-Confirmation Market Fetch" (🟡 MEDIUM — polish existing feature)**
**Current state**: Morning job now fetches and logs today's Kalshi markets with resolution rules

**Proposed enhancement**:
- At drop alert (~4-5 PM), fetch Kalshi markets again
- Show user: "IF final high is 82°F → bracket B81.5 (YES if < 81°F) @ $0.65"
- Early visibility into what you'd be trading → user can validate the rule themselves

**Requirements**:
- Add to drop alert formatter (format_drop_detected_alert)
- One extra Kalshi API call at alert time (won't slow down)

**Expected gain**: Transparency (user catches errors like the B40.5 mislabeling), not time
**Risk**: Low — read-only operation

---

### Recommendation: Phased Approach

**Phase 1 (Now)**:
- ✅ Collect 10 more days of backtest data (you're on day 5)
- ✅ Keep 1°F strict tolerance (accuracy over speed)
- ✅ Use timeout fallback at 7 PM with 1.5°F gap
- ✅ Add pre-confirmation market fetch at drop alert

**Phase 2 (After 10 days)**:
- Analyze T-Group accuracy: does HIGH confidence predict settlement correctly?
- Analyze NWS Obs lag: how often does it match final CLI within 1°F?
- Decide: Confidence-Based (Option 1) or T-Group Shift (Option 2)

**Phase 3 (After 20+ days)**:
- Implement whichever method showed highest accuracy
- Validate with live data before going live

---

## PART 4: CURRENT FIXES SUMMARY

✅ **Bracket matching**: `confirmed_high < low` (correct Kalshi convention)
✅ **Backtest logging**: `bracket_correct = actual < lo` (matches bracket logic)
✅ **T-Group gate**: Uses `drop_detected` (runs immediately after drop)
✅ **T-Group format**: Switched to `format=raw` (robust against API changes)
✅ **Confirmation visibility**: Hold notifications sent every hold #1 and #6
✅ **Timeout confirmation**: At 7 PM, if gap ≤ 1.5°F, force-confirm using closest reading
✅ **Morning markets**: Daily Kalshi market rules message sent at 8 AM
✅ **Dispatch status**: Shows hold detail (CLI vs METAR, exact gap)

