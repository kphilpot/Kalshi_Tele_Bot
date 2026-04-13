"""
Daily Paper Trading Viewer

Simple CLI tool to view results from paper trading runs.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta


def view_summary(date: str = None, log_dir: str = "paper_trading_logs") -> None:
    """
    View daily summary for a specific date.

    Args:
        date: Date string (YYYY-MM-DD). Defaults to yesterday.
        log_dir: Log directory path
    """

    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_path = Path(log_dir) / date / "SUMMARY_all_strategies.json"

    if not log_path.exists():
        print(f"No summary found for {date}")
        print(f"Looked in: {log_path}")
        return

    with open(log_path, "r") as f:
        summary = json.load(f)

    print("\n" + "=" * 100)
    print(f"PAPER TRADING SUMMARY: {date}")
    print("=" * 100)

    if "strategies" in summary:
        for strategy_id, perf in summary["strategies"].items():
            print(f"\n{perf.get('strategy_name', strategy_id)}:")
            print(f"  Trades fired:        {perf.get('trades_fired', 0)}")
            print(f"  Trades won:          {perf.get('trades_won', 0)}")
            print(f"  Trades lost:         {perf.get('trades_lost', 0)}")
            print(f"  Win rate:            {perf.get('win_rate', 'N/A')}")
            print(f"  Total P&L:           ${perf.get('total_pnl', 0):.2f}")
            print(f"  Avg entry price:     ${perf.get('average_entry_price', 0):.2f}")

    print("\n" + "=" * 100)


def view_comparative_report(date: str = None, log_dir: str = "paper_trading_logs") -> None:
    """
    View comparative analysis for a date.

    Args:
        date: Date string (YYYY-MM-DD). Defaults to yesterday.
        log_dir: Log directory path
    """

    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    log_path = Path(log_dir) / date / "COMPARATIVE_REPORT.json"

    if not log_path.exists():
        print(f"No comparative report found for {date}")
        return

    with open(log_path, "r") as f:
        report = json.load(f)

    print("\n" + "=" * 100)
    print(f"COMPARATIVE ANALYSIS: {date}")
    print("=" * 100)

    best = report.get("best_performer", {})
    print(f"\nBest performer: {best.get('strategy_id')}")
    print(f"P&L: ${best.get('total_pnl', 0):.2f}")

    comparison = report.get("comparative_analysis", {}).get("tier1_vs_tier2", {})
    if comparison:
        print(f"\nTIER 1 vs TIER 2:")
        print(f"  TIER 1 P&L:  ${comparison.get('tier1_pnl', 0):.2f} ({comparison.get('tier1_trades', 0)} trades)")
        print(f"  TIER 2 P&L:  ${comparison.get('tier2_pnl', 0):.2f} ({comparison.get('tier2_trades', 0)} trades)")
        print(f"  Difference:  ${comparison.get('difference', 0):.2f}")
        print(f"  Winner:      {comparison.get('winner')}")

    print("\n" + "=" * 100)


def list_all_dates(log_dir: str = "paper_trading_logs") -> None:
    """
    List all dates with available paper trading data.

    Args:
        log_dir: Log directory path
    """

    log_path = Path(log_dir)

    if not log_path.exists():
        print(f"Log directory not found: {log_path}")
        return

    dates = sorted([d.name for d in log_path.iterdir() if d.is_dir()])

    if not dates:
        print("No paper trading data found")
        return

    print("\nAvailable paper trading dates:")
    for date in dates:
        summary_path = log_path / date / "SUMMARY_all_strategies.json"
        status = "✓" if summary_path.exists() else "✗"
        print(f"  {status} {date}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]
        date = sys.argv[2] if len(sys.argv) > 2 else None

        if command == "summary":
            view_summary(date)
        elif command == "comparative":
            view_comparative_report(date)
        elif command == "list":
            list_all_dates()
    else:
        # Default: show summary for today
        view_summary()
        view_comparative_report()
