# Kalshi Bracket Matching Diagnostic Improvements

## Problem Identified

On April 5 & 6, Miami hit 84°F and the bracket search failed **both times**. This pattern suggests either:
1. **Kalshi market availability issue** — No brackets ≥ 85 available on those dates
2. **System bug** — Bracket parsing or API call failing
3. **Rate limiting** — Kalshi API returned 0 markets due to rate limit

**Previous behavior**: The system would just log "No bracket found" without showing what brackets *were* available, making diagnosis impossible.

---

## Changes Made

### 1. Enhanced `kalshi.py` — Better Bracket Diagnostics

**Added detailed logging in `find_bracket_for_temp()`**:

```python
# Now logs:
# - Whether Kalshi returned 0 markets (API failure indicator)
# - All available brackets (e.g., "80-81, 81-82, 82-83, 83-84")
# - For each bracket: the matching logic and result
#   Example: "✓ MATCH 84-85 → 84 < 85" or "✗ 85-86 → 84 < 85"
```

**Benefit**: When bracket-not-found fires, logs will show exactly what Kalshi offered and why it didn't match.

---

### 2. Enhanced `scheduler.py` Step 4.5 — Settlement Audit Logging

**When fetching markets for early bracket prediction**:

```
[KMIA] Settlement audit: fetching Kalshi markets for 84°F (confidence: HIGH)
[KMIA] Settlement audit: Fetched 120 markets from Kalshi
[KMIA] Available brackets: 80-81, 81-82, 82-83, 83-84
[KMIA] Testing temp 84°F against 4 brackets:
  ✗ 80-81 → 84 < 80
  ✗ 81-82 → 84 < 81
  ✗ 82-83 → 84 < 82
  ✗ 83-84 → 84 < 83
[KMIA] ✗ Settlement audit bracket not found (reason: no_bracket_in_range)
```

**Benefit**: Immediately see why the search failed — no bracket has floor ≥ 85.

---

### 3. Enhanced `scheduler.py` Step 6 — CLI Bracket Lookup Logging

**When fetching markets after CLI confirmation**:

```
[KMIA] Step 6: CLI bracket lookup for 84°F (confirmed high)
[KMIA] Fetched 120 markets for bracket lookup
[KMIA] Available brackets: 80-81, 81-82, 82-83, 83-84
[KMIA] Testing temp 84°F against 4 brackets:
  ✗ 80-81 → 84 < 80
  ✗ 81-82 → 84 < 81
  ✗ 82-83 → 84 < 82
  ✗ 83-84 → 84 < 83
[KMIA] ✗ No matching Kalshi bracket for 84°F after CLI confirmation
```

**Benefit**: Shows the exact same market situation at the final confirmation stage.

---

## New Log Patterns to Watch For

### Healthy Bracket Match
```
[KMIA] Available brackets: 80-81, 81-82, 82-83, 83-84, 84-85, 85-86
[KMIA] Testing temp 84°F against 6 brackets:
  ✓ MATCH 85-86 → 84 < 85
```
→ Expected behavior, trade can proceed

### No Markets Returned (API Failure)
```
[KMIA] Fetched 0 markets for bracket lookup
[KMIA] No markets returned from Kalshi API
```
→ **Investigate**: Kalshi API issue, rate limit, or authentication problem

### Markets Available but No Match
```
[KMIA] Fetched 120 markets for bracket lookup
[KMIA] Available brackets: 80-81, 81-82, 82-83, 83-84
[KMIA] ✗ No matching Kalshi bracket for 84°F
```
→ Expected if Kalshi doesn't offer high enough brackets (market availability, not a bug)

### Parsing Error
```
[KMIA] No parsed brackets found in 120 markets
```
→ **Bug alert**: Markets were fetched but bracket extraction failed — check Kalshi API response format

---

## What to Check If Miami Keeps Failing at 84°F

1. **Check the logs for "Available brackets"** — do they show up to 85-86 or higher?
   - **If NO**: Kalshi simply doesn't offer those brackets on that day (market availability, not a bug)
   - **If YES but still fails**: Check the matching logic output — something is wrong with the comparison

2. **Check for "0 markets returned"** — indicates API issue, not a market availability issue
   - Rate limiting
   - Authentication failure
   - Kalshi API downtime

3. **Check for "parsing_error"** — indicates bracket extraction is broken
   - Kalshi changed API response format
   - Regex patterns in `parse_bracket_from_title()` need updating

---

## Test the Diagnostics

Run the diagnostic script to test right now:

```bash
cd "C:\Users\user\Personal_Workspace\02_Projects\Kalshi Tele Bot"
py test_kalshi_connection.py
```

This will:
- Test Kalshi API connection
- Fetch today's markets
- Show all available brackets for each city
- Test bracket matching at 80-85°F range
- Show why each temperature matches or doesn't match

---

## Next Steps If Problem Recurs

If Miami hits 84°F again and bracket-not-found fires:

1. **Check the debug logs** — paste the "Available brackets" line here
2. **Determine the root cause**:
   - No 85+ brackets? → Market availability (expected behavior, not a bug)
   - 0 markets returned? → API issue (needs investigation)
   - Parsing error? → Kalshi format changed (needs code fix)
3. **Report with logs** — the new logging makes it possible to diagnose

---

## Summary

**These changes don't fix the bracket-not-found issue** — they just make it diagnosable.

If Miami at 84°F is a recurring market availability issue (Kalshi doesn't offer 85+ brackets on certain days), that's not a system bug — it's how the market works. The system correctly flags this with an alert so you can manually review.

If it's an API failure (0 markets returned), we'll see it immediately in the logs.

If it's a parsing bug, we'll see "parsing_error" in the logs.

**With these improvements, we'll know exactly what's happening instead of guessing.**
