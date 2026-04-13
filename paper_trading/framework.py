"""
Paper Trading Framework

Multi-strategy engine that feeds METAR data to all enabled strategies
and collects results for comparison.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .logger import PaperTradingLogger
from .strategies import TIER1SettlementAudit, TIER2RateOfChange
from .strategies.base_strategy import BaseStrategy
from .telegram_alerts import PaperTradingTelegramAlerts

logger = logging.getLogger("paper_trading.framework")


class PaperTradingFramework:
    """
    Multi-strategy paper trading engine.

    Manages multiple strategies running in parallel, feeding them the same METAR data,
    collecting decisions, and logging results for comparison.

    Example usage:
        paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2"])

        # Every poll cycle (10 minutes)
        decisions = paper_trader.process_poll_cycle(
            timestamp=datetime.utcnow(),
            station="KAUS",
            city="Austin",
            metar_history=[(time1, temp1), (time2, temp2), ...],
            tgroup_prediction=86.0,
            hrrr_ceiling=88.0
        )

        # At end of day
        paper_trader.end_of_day(
            date="2026-04-13",
            station="KAUS",
            actual_cli_high=85.0
        )
    """

    def __init__(
        self,
        enabled_strategies: List[str] = None,
        telegram_bot=None,
        chat_id: int = None,
    ):
        """
        Initialize framework with desired strategies and optional Telegram alerts.

        Args:
            enabled_strategies: List of strategy IDs to enable
                               (e.g., ["TIER1", "TIER2"])
                               Defaults to ["TIER1", "TIER2"]
            telegram_bot: Optional Telegram bot instance for alerts
            chat_id: Optional chat ID for Telegram alerts
        """

        self.strategies: Dict[str, BaseStrategy] = {}
        self.logger = PaperTradingLogger()

        # Initialize Telegram alerts if provided
        self.telegram_alerts = None
        if telegram_bot and chat_id:
            self.telegram_alerts = PaperTradingTelegramAlerts(
                telegram_bot=telegram_bot, chat_id=chat_id
            )

        if enabled_strategies is None:
            enabled_strategies = ["TIER1", "TIER2"]

        # Initialize requested strategies
        if "TIER1" in enabled_strategies:
            self.strategies["TIER1"] = TIER1SettlementAudit()

        if "TIER2" in enabled_strategies:
            self.strategies["TIER2"] = TIER2RateOfChange()

        logger.info(
            f"[Paper Trading] Framework initialized with strategies: {list(self.strategies.keys())}"
        )

        # Send initialization alert if Telegram is configured
        if self.telegram_alerts:
            try:
                import asyncio

                asyncio.create_task(
                    self.telegram_alerts.send_framework_initialized_alert(
                        enabled_strategies=list(self.strategies.keys())
                    )
                )
            except Exception as e:
                logger.error(f"Failed to send Telegram initialization alert: {e}")

    def process_poll_cycle(
        self,
        timestamp: datetime,
        station: str,
        city: str,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
    ) -> Dict:
        """
        Process one poll cycle through all enabled strategies.

        Called every 10 minutes during trading day.

        Args:
            timestamp: Current UTC time
            station: Station code (e.g., "KAUS")
            city: City name (e.g., "Austin")
            metar_history: List of (timestamp, temp_f) tuples
            tgroup_prediction: T-Group settlement prediction (°F)
            hrrr_ceiling: HRRR model ceiling (°F)

        Returns:
            Dict mapping strategy_id -> decision dict
        """

        decisions = {}

        # Run all enabled strategies
        for strategy_id, strategy in self.strategies.items():
            try:
                decision = strategy.make_decision(
                    metar_history=metar_history,
                    tgroup_prediction=tgroup_prediction,
                    hrrr_ceiling=hrrr_ceiling,
                    current_time=timestamp,
                    station=station,
                    city=city,
                )

                decisions[strategy_id] = decision

                # Log immediately (JSONL format)
                self.logger.log_decision(decision, station)

                # Log to console if trade fired
                if decision["decision"] == "TRADE":
                    logger.info(
                        f"[{station}] {strategy_id} fired TRADE at {timestamp.strftime('%H:%M')} "
                        f"(confidence: {decision['confidence_score']}, entry: ${decision['assumed_entry_price']:.2f})"
                    )

                    # Send Telegram alert if configured
                    if self.telegram_alerts:
                        try:
                            import asyncio

                            asyncio.create_task(
                                self.telegram_alerts.send_trade_fired_alert(
                                    station=station,
                                    city=city,
                                    strategy_id=strategy_id,
                                    strategy_name=strategy.strategy_name,
                                    confidence_score=decision["confidence_score"],
                                    assumed_entry_price=decision["assumed_entry_price"],
                                    expected_value=decision.get(
                                        "expected_value", 0
                                    ),
                                    hour=timestamp.hour,
                                )
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to send Telegram trade alert for {strategy_id}: {e}"
                            )

            except Exception as e:
                logger.error(f"Error in {strategy_id} strategy: {e}", exc_info=True)
                decisions[strategy_id] = {"error": str(e)}

        return decisions

    def end_of_day(
        self,
        date: str,
        station: str,
        actual_cli_high: float,
    ) -> Dict:
        """
        End-of-day processing: back-fill ground truth, generate summaries.

        Called once per station per day, after CLI arrives (~7 PM).

        Args:
            date: Date string (YYYY-MM-DD)
            station: Station code
            actual_cli_high: Actual CLI settlement high (°F)

        Returns:
            Daily summary dict
        """

        logger.info(f"[{station}] End-of-day processing for {date}")

        # Back-fill ground truth for all strategies
        for strategy_id, strategy in self.strategies.items():
            for trade in strategy.trades_fired:
                entry_price = trade["assumed_entry_price"]

                # Win condition: settlement below entry price
                # (market prices YES as if it will settle below)
                # So if actual_cli_high < entry_price, the YES contract wins
                won = actual_cli_high < entry_price

                # Profit/loss calculation
                if won:
                    pnl = (
                        1.00 - entry_price
                    )  # Win: get $1.00, paid entry_price
                else:
                    pnl = -entry_price  # Loss: paid entry_price, get $0.00

                trade["ground_truth"] = {
                    "actual_cli_high": round(actual_cli_high, 1),
                    "entry_price": round(entry_price, 2),
                    "won": won,
                    "pnl": round(pnl, 2),
                }

        # Generate daily summary
        summary = self._generate_daily_summary(date)

        # Save to files
        self.logger.save_daily_summary(summary, date)
        comparative_report = self._generate_comparative_report(date)
        self.logger.save_comparative_report(comparative_report, date)

        # Print to console
        self.logger.print_daily_summary(date)

        # Send Telegram daily summary alert if configured
        if self.telegram_alerts:
            try:
                import asyncio

                asyncio.create_task(
                    self.telegram_alerts.send_daily_summary_alert(
                        date=date,
                        summaries=summary.get("strategies", {}),
                        comparative=comparative_report.get(
                            "comparative_analysis", {}
                        ),
                    )
                )
            except Exception as e:
                logger.error(f"Failed to send Telegram daily summary alert: {e}")

        return summary

    def _generate_daily_summary(self, date: str) -> Dict:
        """
        Generate daily summary: all strategies' performance.

        Args:
            date: Date string (YYYY-MM-DD)

        Returns:
            Summary dict
        """

        return {
            "date": date,
            "strategies": {
                strategy_id: strategy.get_performance_summary()
                for strategy_id, strategy in self.strategies.items()
            },
        }

    def _generate_comparative_report(self, date: str) -> Dict:
        """
        Generate detailed comparative analysis.

        Args:
            date: Date string (YYYY-MM-DD)

        Returns:
            Comparative report dict
        """

        # Get all summaries
        summaries = {
            strategy_id: strategy.get_performance_summary()
            for strategy_id, strategy in self.strategies.items()
        }

        # Find best performer
        best_strategy = None
        best_pnl = float("-inf")

        for strategy_id, summary in summaries.items():
            pnl = summary.get("total_pnl", 0)
            if pnl > best_pnl:
                best_pnl = pnl
                best_strategy = strategy_id

        # Build report
        report = {
            "date": date,
            "strategies": summaries,
            "best_performer": {
                "strategy_id": best_strategy,
                "total_pnl": best_pnl,
            },
            "comparative_analysis": {
                "tier1_vs_tier2": self._compare_tier1_vs_tier2(summaries),
            },
        }

        return report

    @staticmethod
    def _compare_tier1_vs_tier2(summaries: Dict) -> Dict:
        """
        Compare TIER 1 and TIER 2 specifically.

        Args:
            summaries: Strategy summaries dict

        Returns:
            Comparison dict
        """

        tier1 = summaries.get("TIER1", {})
        tier2 = summaries.get("TIER2", {})

        tier1_pnl = tier1.get("total_pnl", 0)
        tier2_pnl = tier2.get("total_pnl", 0)
        pnl_diff = tier2_pnl - tier1_pnl

        return {
            "tier1_pnl": round(tier1_pnl, 2),
            "tier2_pnl": round(tier2_pnl, 2),
            "difference": round(pnl_diff, 2),
            "winner": "TIER2" if pnl_diff > 0 else "TIER1" if pnl_diff < 0 else "TIE",
            "tier1_trades": tier1.get("trades_fired", 0),
            "tier2_trades": tier2.get("trades_fired", 0),
        }

    def add_custom_strategy(self, strategy: BaseStrategy) -> None:
        """
        Add a custom strategy at runtime.

        Useful for testing new strategies without modifying framework.

        Args:
            strategy: Instance of a strategy (must inherit BaseStrategy)
        """

        self.strategies[strategy.strategy_id] = strategy
        logger.info(f"[Paper Trading] Added custom strategy: {strategy.strategy_name}")

    def get_strategy_decisions(self, date: str, strategy_id: str, station: str) -> List:
        """
        Retrieve all decisions for a specific strategy/date/station.

        Args:
            date: Date string (YYYY-MM-DD)
            strategy_id: Strategy ID (e.g., "TIER2")
            station: Station code (e.g., "KAUS")

        Returns:
            List of decision dicts
        """

        return self.logger.get_decisions_for_strategy(date, strategy_id, station)
