"""
Base Strategy Class

Abstract base class for all paper trading strategies.
All concrete strategies must inherit from this and implement the abstract methods.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from ..config import KALSHI_PRICE_MODEL


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Each strategy implements:
    - calculate_confidence_score(): Core scoring logic
    - should_trade(): Decision threshold
    - get_decision_details(): Strategy-specific details for logging
    """

    def __init__(self, strategy_name: str, strategy_id: str):
        """
        Initialize strategy.

        Args:
            strategy_name: Human-readable name (e.g., "TIER 2: Rate-of-Change")
            strategy_id: Short ID for logging (e.g., "TIER2")
        """
        self.strategy_name = strategy_name
        self.strategy_id = strategy_id
        self.trades_fired = []
        self.decision_history = []

    @abstractmethod
    def calculate_confidence_score(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
        current_time: datetime,
    ) -> int:
        """
        Calculate confidence score for trading decision.

        Args:
            metar_history: List of (timestamp, temp_f) tuples
            tgroup_prediction: T-Group settlement prediction (°F)
            hrrr_ceiling: HRRR model ceiling/max (°F)
            current_time: Current local time

        Returns:
            Confidence score (0-100)
        """
        pass

    @abstractmethod
    def should_trade(self, confidence_score: int) -> bool:
        """
        Determine if confidence score exceeds trading threshold.

        Args:
            confidence_score: Score from calculate_confidence_score()

        Returns:
            True if should trade, False otherwise
        """
        pass

    @abstractmethod
    def get_decision_details(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
    ) -> Dict:
        """
        Return strategy-specific details for logging.

        Different strategies log different information:
        - TIER1: drop confirmation, lock status
        - TIER2: velocities, accelerations, gate statuses

        Args:
            metar_history: List of (timestamp, temp_f) tuples
            tgroup_prediction: T-Group prediction
            hrrr_ceiling: HRRR ceiling

        Returns:
            Dict with strategy-specific details
        """
        pass

    def make_decision(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
        current_time: datetime,
        station: str,
        city: str,
    ) -> Dict:
        """
        Make trading decision based on current data.

        Called every poll cycle. Calculates score, determines if should trade,
        logs decision.

        Args:
            metar_history: List of (timestamp, temp_f) tuples
            tgroup_prediction: T-Group settlement prediction
            hrrr_ceiling: HRRR ceiling
            current_time: Current local time
            station: Station code (e.g., "KAUS")
            city: City name (e.g., "Austin")

        Returns:
            Decision dict with all details
        """

        # Calculate confidence
        confidence_score = self.calculate_confidence_score(
            metar_history, tgroup_prediction, hrrr_ceiling, current_time
        )

        # Determine if should trade
        trade_decision = self.should_trade(confidence_score)

        # Get assumed entry price based on time of day
        assumed_price = self._get_assumed_price(current_time.hour)

        # Calculate expected value
        expected_value = self._calculate_expected_value(confidence_score, assumed_price)

        # Get strategy-specific details
        details = self.get_decision_details(metar_history, tgroup_prediction, hrrr_ceiling)

        # Build decision package
        decision_package = {
            "timestamp_utc": current_time.isoformat(),
            "timestamp_local": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "hour": current_time.hour,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "station": station,
            "city": city,
            "confidence_score": confidence_score,
            "decision": "TRADE" if trade_decision else "NO_TRADE",
            "assumed_entry_price": round(assumed_price, 2),
            "expected_value": round(expected_value, 4),
            "details": details,
            "ground_truth": None,  # Will be filled at EOD
        }

        # Track in history
        self.decision_history.append(decision_package)

        # If trade decision, also track in trades
        if trade_decision:
            self.trades_fired.append(decision_package)

        return decision_package

    @staticmethod
    def _get_assumed_price(hour: int) -> float:
        """
        Get realistic Kalshi YES ask price based on time of day.

        Args:
            hour: Hour of day (0-23)

        Returns:
            Assumed entry price ($0.00 - $1.00)
        """
        return KALSHI_PRICE_MODEL.get(hour, 0.50)

    def _calculate_expected_value(self, confidence: int, entry_price: float) -> float:
        """
        Calculate expected value of a trade.

        E[V] = P(win) × profit + P(loss) × loss

        Args:
            confidence: Confidence percentage (0-100)
            entry_price: Entry price ($0.00 - $1.00)

        Returns:
            Expected value in dollars
        """
        win_payout = 1.00
        profit_if_win = win_payout - entry_price
        loss_if_lose = -entry_price

        expected_val = (confidence / 100) * profit_if_win + (
            1 - confidence / 100
        ) * loss_if_lose

        return expected_val

    def get_performance_summary(self) -> Dict:
        """
        Get end-of-day performance summary for this strategy.

        Returns:
            Dict with trades_fired, win_rate, total_pnl, etc.
        """

        trades = self.trades_fired

        if not trades:
            return {
                "strategy_id": self.strategy_id,
                "strategy_name": self.strategy_name,
                "trades_fired": 0,
                "trades_won": 0,
                "trades_lost": 0,
                "win_rate": None,
                "total_pnl": 0.0,
                "average_entry_price": None,
            }

        # Count wins/losses
        wins = sum(1 for t in trades if t.get("ground_truth", {}).get("won", False))
        losses = len(trades) - wins

        # Sum P&L
        total_pnl = sum(t.get("ground_truth", {}).get("pnl", 0) for t in trades)

        # Average entry price
        avg_entry = sum(t.get("assumed_entry_price", 0) for t in trades) / len(
            trades
        )

        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "trades_fired": len(trades),
            "trades_won": wins,
            "trades_lost": losses,
            "win_rate": f"{wins / len(trades) * 100:.1f}%" if trades else None,
            "total_pnl": round(total_pnl, 2),
            "average_entry_price": round(avg_entry, 2),
        }
