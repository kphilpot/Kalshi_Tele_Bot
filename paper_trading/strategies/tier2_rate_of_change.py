"""
TIER 2: Rate-of-Change Peak Detection Strategy

Detects when peak is imminent by monitoring temperature acceleration.
Incorporates: velocity decline, flatness check, safety gates.
"""

from datetime import datetime
from typing import Dict, List, Tuple
from .base_strategy import BaseStrategy
from ..config import CONFIDENCE_SCORE_WEIGHTS, SAFETY_GATES, SOLAR_TIME_MULTIPLIERS


class TIER2RateOfChange(BaseStrategy):
    """
    TIER 2: Rate-of-Change Peak Detection

    Strategy:
    - Calculates temperature velocity (rate of rise)
    - Detects when velocity is declining (acceleration < 0)
    - When rate declines AND temp near T-Group → peak imminent
    - Fires trade 30-60 minutes before actual drop

    Safety mechanisms:
    - Check Engine Light: aborts if current > model by >1.5°F
    - Small Numbers Gate: requires V1 > 0.5°F/hr
    - Solar Time Multiplier: weights decisions by time of day
    """

    def __init__(self):
        super().__init__(
            strategy_name="TIER 2: Rate-of-Change Peak Detection",
            strategy_id="TIER2",
        )
        self.confidence_threshold = 70  # Trade if score >= 70

    def calculate_confidence_score(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
        current_time: datetime,
    ) -> int:
        """
        Calculate confidence using velocity, acceleration, and safety gates.

        Returns:
            Confidence score (0-100)
        """

        # Need at least 3 readings
        if len(metar_history) < 3:
            return 0

        # Extract last 3 temperatures
        temps = [reading[1] for reading in metar_history[-3:]]
        current_temp = temps[-1]

        # ===== GATE 1: CHECK ENGINE LIGHT =====
        # If reality has already diverged from model, model is broken
        model_average = (tgroup_prediction + hrrr_ceiling) / 2
        model_error = current_temp - model_average

        if model_error > SAFETY_GATES["check_engine_light_threshold"]:
            # Model is broken, don't trade
            return 0

        # ===== VELOCITY & ACCELERATION CALCULATION =====
        # Assuming hourly METAR readings (10:53, 11:53, 12:53, etc.)
        v1 = temps[1] - temps[0]  # Rise in first hour (°F)
        v2 = temps[2] - temps[1]  # Rise in second hour (°F)

        # ===== GATE 2: SMALL NUMBERS TRAP =====
        # If first hour rise was tiny, we're not in meaningful heating phase
        if v1 < SAFETY_GATES["small_numbers_gate_threshold"]:
            return 0

        # ===== CONFIDENCE SCORING (0-100) =====
        score = 0

        # 1. Rate decline check (0-40 points)
        # Higher score if rate is declining sharply
        if v2 > 0:  # Still rising (not cooling)
            rate_decline_pct = ((v1 - v2) / v1) * 100 if v1 != 0 else 0

            if rate_decline_pct >= 50:
                score += CONFIDENCE_SCORE_WEIGHTS["rate_decline"]  # 40 points
            elif rate_decline_pct >= 30:
                score += 25
            elif rate_decline_pct >= 10:
                score += 10
        elif v2 <= 0:
            # Actually cooling down → very high confidence peak occurred
            score += CONFIDENCE_SCORE_WEIGHTS["rate_decline"]

        # 2. Velocity level check (0-30 points)
        # Higher score if current rise rate is very small (plateau)
        if v2 < 0.5:
            score += CONFIDENCE_SCORE_WEIGHTS["velocity_level"]  # 30 points
        elif v2 < 1.0:
            score += 20
        elif v2 < 2.0:
            score += 10

        # 3. Proximity to T-Group (0-20 points)
        # Higher score if current temp matches prediction
        temp_diff = abs(current_temp - tgroup_prediction)

        if temp_diff <= 1.0:
            score += CONFIDENCE_SCORE_WEIGHTS["tgroup_proximity"]  # 20 points
        elif temp_diff <= 2.0:
            score += 10
        elif temp_diff <= 3.0:
            score += 5

        # 4. Flatness bonus (0-10 points)
        # Bonus if most recent hour showed very little rise
        recent_change = abs(temps[2] - temps[1])

        if recent_change < 0.5:
            score += CONFIDENCE_SCORE_WEIGHTS["flatness"]  # 10 points

        # ===== GATE 3: SOLAR TIME MULTIPLIER =====
        # Weight scores based on time of day (physics of solar heating)
        hour = current_time.hour
        time_multiplier = SOLAR_TIME_MULTIPLIERS.get(hour, 0.5)

        final_score = int(score * time_multiplier)

        return min(final_score, 100)

    def should_trade(self, confidence_score: int) -> bool:
        """
        Trade if confidence >= threshold (default 70).

        Args:
            confidence_score: Score from calculate_confidence_score()

        Returns:
            True if score >= threshold
        """
        return confidence_score >= self.confidence_threshold

    def get_decision_details(
        self,
        metar_history: List[Tuple[datetime, float]],
        tgroup_prediction: float,
        hrrr_ceiling: float,
    ) -> Dict:
        """
        Return TIER2-specific details: velocities, accelerations, gate statuses.

        Args:
            metar_history: Last 3+ METAR readings
            tgroup_prediction: T-Group prediction
            hrrr_ceiling: HRRR ceiling

        Returns:
            Dict with TIER2 details
        """

        if len(metar_history) < 3:
            return {"error": "Insufficient data"}

        temps = [reading[1] for reading in metar_history[-3:]]
        current_temp = temps[-1]
        v1 = temps[1] - temps[0]
        v2 = temps[2] - temps[1]
        model_average = (tgroup_prediction + hrrr_ceiling) / 2
        model_error = current_temp - model_average

        rate_decline_pct = ((v1 - v2) / v1 * 100) if v1 != 0 else 0

        return {
            "approach": "TIER 2 Rate-of-Change Detection",
            "temps_last_3_hours": [round(t, 1) for t in temps],
            "v1_first_hour_fph": round(v1, 2),
            "v2_second_hour_fph": round(v2, 2),
            "acceleration_fph": round(v2 - v1, 2),
            "rate_decline_pct": round(rate_decline_pct, 1),
            "current_vs_tgroup_diff": round(abs(current_temp - tgroup_prediction), 2),
            "model_error": round(model_error, 2),
            "gates": {
                "check_engine_light": "PASS" if model_error <= 1.5 else "FAIL",
                "small_numbers_gate": "PASS" if v1 >= 0.5 else "FAIL",
                "solar_gate": "PASS",
            },
            "interpretation": self._interpret_signal(v1, v2, rate_decline_pct),
        }

    @staticmethod
    def _interpret_signal(v1: float, v2: float, rate_decline_pct: float) -> str:
        """
        Human-readable interpretation of the signal.

        Args:
            v1: Rise rate in first hour
            v2: Rise rate in second hour
            rate_decline_pct: Percentage decline in rate

        Returns:
            String interpretation
        """

        if v2 <= 0:
            return "Peak likely passed (temps cooling)"
        elif rate_decline_pct >= 50:
            return "Peak imminent (rate declining >50%)"
        elif v2 < 0.5:
            return "Temps plateauing (rise nearly flat)"
        elif rate_decline_pct >= 30:
            return "Peak approaching (moderate rate decline)"
        else:
            return "Still rising steadily (peak not imminent)"
