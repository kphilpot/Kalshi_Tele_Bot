#!/usr/bin/env python3
"""
backtest/analyze.py — Analyze recorded backtest data.

Usage:
    python backtest/analyze.py
    python backtest/analyze.py --city KMIA
    python backtest/analyze.py --city KMIA KMDW
    python backtest/analyze.py --from 2026-03-01 --to 2026-03-28
    python backtest/analyze.py --verbose          (day-by-day table)

Run from the Kalshi Tele Bot root directory.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Allow running as: python backtest/analyze.py from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import BACKTEST_STARTING_BANK  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"

CITY_ORDER = ["KMIA", "KMDW", "KAUS"]
CITY_LABELS = {"KMIA": "Miami", "KAUS": "Austin", "KMDW": "Chicago"}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_records(city_filter=None, date_from=None, date_to=None):
    if not DATA_DIR.exists() or not any(DATA_DIR.iterdir()):
        print("No backtest data found in backtest/data/.")
        print("The bot writes a record each night at 10 PM EST after the EOD job runs.")
        sys.exit(0)

    records = []
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
            station = rec["meta"]["station"]
            rec_date = date.fromisoformat(rec["meta"]["date"])

            if city_filter and station not in city_filter:
                continue
            if date_from and rec_date < date_from:
                continue
            if date_to and rec_date > date_to:
                continue

            records.append(rec)
        except Exception as exc:
            print(f"  Warning: could not load {path.name}: {exc}", file=sys.stderr)

    return records


# ---------------------------------------------------------------------------
# Summarise
# ---------------------------------------------------------------------------

def summarize(records, verbose=False):
    by_city = {}
    for rec in records:
        s = rec["meta"]["station"]
        by_city.setdefault(s, []).append(rec)

    date_range = ""
    if records:
        dates = sorted(r["meta"]["date"] for r in records)
        date_range = f"  {dates[0]}  ->  {dates[-1]}"

    print()
    print("=" * 72)
    print(f"  BACKTEST ANALYSIS — {len(records)} records  {date_range}")
    print("=" * 72)

    for station in CITY_ORDER:
        if station not in by_city:
            continue
        recs = by_city[station]
        city = CITY_LABELS.get(station, station)

        total = len(recs)
        drop_days    = sum(1 for r in recs if r["detection"]["drop_detected"])
        lock_days    = sum(1 for r in recs if r["triple_lock"]["triple_lock_passed"])
        alert_days   = sum(1 for r in recs if r["alerts"]["alert_fired"])
        cli_days     = sum(1 for r in recs if r["ground_truth"]["cli_confirmed"])
        timeout_days = sum(1 for r in recs if r["alerts"]["dsm_timeout_fired"])

        # Bracket accuracy (only days where CLI confirmed and bracket known)
        acc_recs = [r for r in recs if r["ground_truth"]["bracket_correct"] is not None]
        correct  = sum(1 for r in acc_recs if r["ground_truth"]["bracket_correct"])
        acc_pct  = (correct / len(acc_recs) * 100) if acc_recs else None

        # T-Group settlement prediction accuracy
        tg_recs    = [r for r in recs if r["ground_truth"]["settlement_prediction_correct"] is not None]
        tg_correct = sum(1 for r in tg_recs if r["ground_truth"]["settlement_prediction_correct"])
        tg_pct     = (tg_correct / len(tg_recs) * 100) if tg_recs else None

        # Confidence distribution
        conf_dist = {"HIGH": 0, "CAUTION": 0, "WARNING": 0, "FAIL_OPEN": 0, "none": 0}
        for r in recs:
            c = r["settlement_audit"]["confidence"] or "none"
            conf_dist[c] = conf_dist.get(c, 0) + 1

        # Economics
        profit_vals = [
            r["economics"]["potential_profit_cents"]
            for r in recs
            if r["economics"]["potential_profit_cents"] is not None
        ]
        tradeable_recs  = [r for r in recs if r["economics"]["tradeable"] is not None]
        tradeable_count = sum(1 for r in tradeable_recs if r["economics"]["tradeable"])

        max_entry = recs[0]["setup"].get("max_entry_price_cents", 90) if recs else 90
        lock2_tol = recs[0]["setup"].get("lock2_tolerance_f", 3.0) if recs else 3.0

        print()
        print(f"  {station} ({city})")
        print(f"  {'─' * 60}")
        print(f"  Days recorded:              {total}")
        print(f"  Drop detected:              {drop_days}/{total}")
        print(f"  Triple-Lock passed:         {lock_days}/{total}")
        print(f"  Alert fired:                {alert_days}/{total}")
        print(f"  CLI confirmed:              {cli_days}/{total}")
        print(f"  DSM timeout:                {timeout_days}")
        print()
        if acc_pct is not None:
            flag = "  ✅" if acc_pct >= 90 else ("  ⚠️" if acc_pct >= 85 else "  ❌")
            print(f"  Bracket accuracy:    {correct}/{len(acc_recs)}  ({acc_pct:.1f}%){flag}")
        else:
            print(f"  Bracket accuracy:           no CLI data yet")
        if tg_pct is not None:
            print(f"  T-Group prediction:  {tg_correct}/{len(tg_recs)}  ({tg_pct:.1f}%)")
        print()
        print(f"  Confidence distribution:")
        print(f"    HIGH:      {conf_dist.get('HIGH', 0)}")
        print(f"    CAUTION:   {conf_dist.get('CAUTION', 0)}")
        print(f"    WARNING:   {conf_dist.get('WARNING', 0)}")
        print(f"    FAIL_OPEN: {conf_dist.get('FAIL_OPEN', 0)}")
        print()
        print(f"  Settings:  price ceiling={max_entry}¢  lock2_tol={lock2_tol}°F")
        print()
        print(f"  Economics:")
        if profit_vals:
            avg_p = sum(profit_vals) / len(profit_vals)
            print(f"    Avg potential profit:       {avg_p:.0f}¢  "
                  f"(range: {min(profit_vals):.0f}¢ – {max(profit_vals):.0f}¢)")
            if tradeable_recs:
                print(f"    Tradeable entries:          {tradeable_count}/{len(tradeable_recs)}"
                      f"  (price ≤ {max_entry}¢)")
            # Days with ≥10¢ profit
            ten_cent_days = sum(1 for p in profit_vals if p >= 10)
            print(f"    Days with ≥10¢ potential:   {ten_cent_days}/{len(profit_vals)}")
        else:
            print(f"    No price data yet.")
            print(f"    Prices populate after the first full trading day where a signal fires.")

        # P&L accounting (uses .get() for backward compat with pre-P&L records)
        trade_recs = [r for r in recs if r["economics"].get("trade_outcome") is not None]
        wins      = sum(1 for r in trade_recs if r["economics"]["trade_outcome"] == "win")
        losses    = sum(1 for r in trade_recs if r["economics"]["trade_outcome"] == "loss")
        no_trades = sum(1 for r in trade_recs if r["economics"]["trade_outcome"] == "no_trade")
        pending   = sum(1 for r in trade_recs if r["economics"]["trade_outcome"] == "pending")
        city_net  = sum(r["economics"].get("actual_pnl", 0.0) for r in trade_recs)
        sign = "+" if city_net >= 0 else ""
        print(f"  P&L:  W{wins} / L{losses} / NT{no_trades} / P{pending}   Net: {sign}${city_net:.2f}")

        if verbose:
            _print_day_table(recs, station)

    # Portfolio summary across all cities
    all_pnl = sum(
        r["economics"].get("actual_pnl", 0.0)
        for r in records
        if r["economics"].get("trade_outcome") not in (None, "no_trade", "pending")
    )
    final_bal = BACKTEST_STARTING_BANK + all_pnl
    sign = "+" if all_pnl >= 0 else ""
    print()
    print(f"  Portfolio: Starting ${BACKTEST_STARTING_BANK:.2f}  |  Net P&L: {sign}${all_pnl:.2f}  |  Balance: ${final_bal:.2f}")
    print()
    print("=" * 72)
    print()


def _print_day_table(recs, station):
    print()
    hdr = (
        f"    {'Date':<12} {'High':>5} {'Pred':>5} {'CLI':>5} {'Conf':<10} "
        f"{'Price':>6} {'Profit':>7} {'OK?':<5} {'TL':<4} {'Outcome':<9} {'P&L':>8} {'Balance':>9}"
    )
    print(hdr)
    print(
        f"    {'─'*12} {'─'*5} {'─'*5} {'─'*5} {'─'*10} "
        f"{'─'*6} {'─'*7} {'─'*5} {'─'*4} {'─'*9} {'─'*8} {'─'*9}"
    )
    for r in sorted(recs, key=lambda x: x["meta"]["date"]):
        d        = r["meta"]["date"]
        high     = r["detection"]["suspected_high_f"]
        pred     = r["settlement_audit"]["predicted_settlement_f"]
        cli_v    = r["ground_truth"]["cli_high_f"]
        conf     = (r["settlement_audit"]["confidence"] or "NONE")[:10]
        price    = r["economics"]["price_at_settlement_audit"]
        profit   = r["economics"]["potential_profit_cents"]
        correct  = r["ground_truth"]["bracket_correct"]
        tl       = "✓" if r["triple_lock"]["triple_lock_passed"] else "✗"
        outcome  = r["economics"].get("trade_outcome") or "—"
        row_pnl  = r["economics"].get("actual_pnl") or 0.0
        open_bal = r["economics"].get("opening_balance_dollars")
        close_bal = round(open_bal + row_pnl, 2) if open_bal is not None else None

        hs = f"{high:.0f}" if high is not None else "—"
        ps = f"{pred:.0f}" if pred is not None else "—"
        cs = f"{cli_v:.0f}" if cli_v is not None else "—"
        price_s  = f"{round(price*100)}¢" if price is not None else "—"
        profit_s = f"{profit:.0f}¢" if profit is not None else "—"
        ok_s     = "✓" if correct else ("✗" if correct is False else "?")
        pnl_s    = (f"+${row_pnl:.2f}" if row_pnl > 0 else (f"-${abs(row_pnl):.2f}" if row_pnl < 0 else "—")) if outcome not in ("no_trade", "—") else "—"
        bal_s    = f"${close_bal:.2f}" if close_bal is not None else "—"

        print(
            f"    {d:<12} {hs:>5} {ps:>5} {cs:>5} {conf:<10} "
            f"{price_s:>6} {profit_s:>7} {ok_s:<5} {tl:<4} {outcome:<9} {pnl_s:>8} {bal_s:>9}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Ensure Unicode output works on Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Analyze Kalshi bot backtest records",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--city", nargs="+", metavar="STATION",
        help="Filter by station code: KMIA, KAUS, KMDW",
    )
    parser.add_argument(
        "--from", dest="date_from", metavar="YYYY-MM-DD",
        help="Earliest date to include (inclusive)",
    )
    parser.add_argument(
        "--to", dest="date_to", metavar="YYYY-MM-DD",
        help="Latest date to include (inclusive)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show day-by-day breakdown table for each city",
    )
    args = parser.parse_args()

    city_filter = set(args.city) if args.city else None
    date_from   = date.fromisoformat(args.date_from) if args.date_from else None
    date_to     = date.fromisoformat(args.date_to) if args.date_to else None

    records = load_records(city_filter, date_from, date_to)
    if not records:
        print("No matching records found.")
        sys.exit(0)

    summarize(records, verbose=args.verbose)


if __name__ == "__main__":
    main()
