"""
Paper Trading Logger

Handles all logging for the paper trading framework.
Keeps each strategy's data separate but queryable.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("paper_trading")


class PaperTradingLogger:
    """
    Centralized logging for all strategies.

    Directory structure:
    paper_trading_logs/
    ├── 2026-04-13/
    │   ├── TIER1_KAUS.jsonl
    │   ├── TIER1_KMDW.jsonl
    │   ├── TIER1_KMIA.jsonl
    │   ├── TIER2_KAUS.jsonl
    │   ├── TIER2_KMDW.jsonl
    │   ├── TIER2_KMIA.jsonl
    │   ├── SUMMARY_all_strategies.json
    │   └── COMPARATIVE_REPORT.json
    └── 2026-04-14/
    """

    def __init__(self, log_dir: str = "paper_trading_logs"):
        """
        Initialize logger with base directory.

        Args:
            log_dir: Root directory for all paper trading logs
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True, parents=True)

        logger.info(f"[Paper Trading] Logger initialized at {self.log_dir}")

    def log_decision(self, decision: dict, station: str) -> None:
        """
        Log a single decision from a strategy.

        File structure: YYYY-MM-DD/{STRATEGY_ID}_{STATION}.jsonl
        Format: One JSON object per line (JSONL)

        Args:
            decision: Decision dict from strategy.make_decision()
            station: Station code (e.g., "KAUS")
        """

        # Extract date from timestamp
        timestamp = decision.get("timestamp_utc", datetime.now(timezone.utc).isoformat())
        date = timestamp[:10]

        # Create date directory
        date_dir = self.log_dir / date
        date_dir.mkdir(exist_ok=True, parents=True)

        # Get strategy ID
        strategy_id = decision.get("strategy_id", "UNKNOWN")

        # Filename: TIER1_KAUS.jsonl
        filename = date_dir / f"{strategy_id}_{station}.jsonl"

        # Append decision to file (one per line)
        try:
            with open(filename, "a") as f:
                f.write(json.dumps(decision) + "\n")
        except Exception as e:
            logger.error(f"Failed to log decision to {filename}: {e}")

    def save_daily_summary(self, summary: dict, date: str) -> Path:
        """
        Save end-of-day comparative summary for all strategies.

        Args:
            summary: Summary dict from framework._generate_daily_summary()
            date: Date string (YYYY-MM-DD)

        Returns:
            Path to saved file
        """

        date_dir = self.log_dir / date
        date_dir.mkdir(exist_ok=True, parents=True)

        filename = date_dir / "SUMMARY_all_strategies.json"

        try:
            with open(filename, "w") as f:
                json.dump(summary, f, indent=2)

            logger.info(f"[Paper Trading] Saved daily summary: {filename}")
            return filename
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")
            return None

    def save_comparative_report(self, report: dict, date: str) -> Path:
        """
        Save detailed comparative analysis (which strategy won, etc).

        Args:
            report: Comparative analysis dict
            date: Date string (YYYY-MM-DD)

        Returns:
            Path to saved file
        """

        date_dir = self.log_dir / date
        date_dir.mkdir(exist_ok=True, parents=True)

        filename = date_dir / "COMPARATIVE_REPORT.json"

        try:
            with open(filename, "w") as f:
                json.dump(report, f, indent=2)

            logger.info(f"[Paper Trading] Saved comparative report: {filename}")
            return filename
        except Exception as e:
            logger.error(f"Failed to save comparative report: {e}")
            return None

    def get_decisions_for_strategy(self, date: str, strategy_id: str, station: str) -> list:
        """
        Retrieve all decisions for a specific strategy/station/date.

        Args:
            date: Date string (YYYY-MM-DD)
            strategy_id: Strategy ID (e.g., "TIER2")
            station: Station code (e.g., "KAUS")

        Returns:
            List of decision dicts, or empty list if file doesn't exist
        """

        filename = self.log_dir / date / f"{strategy_id}_{station}.jsonl"

        if not filename.exists():
            return []

        decisions = []
        try:
            with open(filename, "r") as f:
                for line in f:
                    if line.strip():
                        decisions.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read decisions from {filename}: {e}")

        return decisions

    def print_daily_summary(self, date: str) -> None:
        """
        Pretty-print the daily summary for human review.

        Args:
            date: Date string (YYYY-MM-DD)
        """

        filename = self.log_dir / date / "SUMMARY_all_strategies.json"

        if not filename.exists():
            print(f"No summary found for {date}")
            return

        try:
            with open(filename, "r") as f:
                summary = json.load(f)

            print("\n" + "=" * 100)
            print(f"PAPER TRADING SUMMARY: {date}")
            print("=" * 100)

            if "strategies" in summary:
                for strategy_id, perf in summary["strategies"].items():
                    print(f"\n{perf.get('strategy_name', strategy_id)}:")
                    print(f"  Trades fired: {perf.get('trades_fired', 0)}")
                    print(f"  Wins: {perf.get('trades_won', 0)}")
                    print(f"  Losses: {perf.get('trades_lost', 0)}")
                    print(f"  Win rate: {perf.get('win_rate', 'N/A')}")
                    print(f"  Total P&L: ${perf.get('total_pnl', 0):.2f}")
                    print(f"  Avg entry price: ${perf.get('average_entry_price', 0):.2f}")

            print("\n" + "=" * 100)

        except Exception as e:
            logger.error(f"Failed to print summary: {e}")
