"""
Paper Trading System for Multi-Strategy Backtesting

This module provides a framework for running multiple trading strategies in parallel
on live data without affecting the main bot. Each strategy makes independent decisions
and all results are logged for comparison.

Usage:
    from paper_trading.framework import PaperTradingFramework

    # Initialize with desired strategies
    paper_trader = PaperTradingFramework(enabled_strategies=["TIER1", "TIER2"])

    # Feed data to all strategies (called every poll cycle)
    decisions = paper_trader.process_poll_cycle(
        timestamp=datetime.utcnow(),
        station="KAUS",
        city="Austin",
        metar_history=[(time1, temp1), (time2, temp2), ...],
        tgroup_prediction=86.0,
        hrrr_ceiling=88.0
    )

    # At end of day, back-fill actual results
    paper_trader.end_of_day(
        date="2026-04-13",
        station="KAUS",
        actual_cli_high=85.0
    )
"""

from .framework import PaperTradingFramework

__all__ = ["PaperTradingFramework"]
