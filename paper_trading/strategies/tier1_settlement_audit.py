"""
TIER 1: Settlement Audit Strategy (Conservative Baseline)

This is the baseline strategy: trade only at 3-4 PM when settlement audit
is ready and we have high confidence.

Used as comparison point to measure if TIER 2 early entries are worth the risk.
"""

from datetime import datetime
from typing import Dict, List, Tuple
from .base_strategy import BaseStrategy


class TIER1SettlementAudit(BaseStrategy):
    """
    TIER 1: Settlement Audit Only (Conservative)

    Strategy:
    - Wait for 3 PM time window (settlement audit runs)
    - High confidence that peak has occurred
    - Trade on settlement audit bracket match
    - Very safe but late entry

    Threshold: 95/100 confidence (only trades 3-4 PM)
    """

    def __init__(self):
        super().__init__(
            strategy_name="TIER 1: Settlement Audit (Conservative)",
            strategy_id="TIER1",
        )
        self.confidence_threshold = 95  # Very high threshold

    def calculate_confidence_score(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
        current_time: datetime,
    ) -> int:
        """
        Calculate confidence based on time of day.

        TIER 1 is time-based: high confidence only at 3-4 PM.

        Args:
            metar_history: Not used for TIER 1
            tgroup_prediction: Not used for TIER 1
            hrrr_ceiling: Not used for TIER 1
            current_time: Current local time (key variable)

        Returns:
            95 if 3-4 PM, else 0
        """

        hour = current_time.hour

        # High confidence at settlement audit time (3-4 PM)
        if hour in [15, 16]:
            return 95

        # No confidence outside this window
        return 0

    def should_trade(self, confidence_score: int) -> bool:
        """
        Trade only if confidence >= 95 (i.e., 3-4 PM only).

        Args:
            confidence_score: From calculate_confidence_score()

        Returns:
            True only during 3-4 PM window
        """

        return confidence_score >= self.confidence_threshold

    def get_decision_details(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
    ) -> Dict:
        """
        Return TIER1-specific details.

        Args:
            metar_history: Not used
            tgroup_prediction: T-Group prediction
            hrrr_ceiling: HRRR ceiling

        Returns:
            Dict with TIER1 details
        """

        return {
            "approach": "TIER 1 Settlement Audit",
            "trigger": "3-4 PM time window",
            "rationale": "Settlement audit ready, high confidence peak occurred",
            "tgroup_prediction": round(tgroup_prediction, 1),
            "hrrr_ceiling": round(hrrr_ceiling, 1),
            "characteristics": {
                "entry_time": "3-4 PM (late)",
                "confidence": "Very high (95%+)",
                "data_freshness": "Very fresh (peak just confirmed)",
                "trade_goal": "Conservative, proven strategy",
            },
        }
