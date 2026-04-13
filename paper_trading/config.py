"""
Paper Trading Configuration

Global settings for all strategies: price models, thresholds, risk parameters.
"""

# Kalshi Price Model: Realistic YES ask prices by time of day
KALSHI_PRICE_MODEL = {
    11: 0.15,   # 11 AM: ultra-early, low confidence, cheap
    12: 0.25,   # 12 PM: still early
    13: 0.35,   # 1 PM: early-mid sweet spot
    14: 0.50,   # 2 PM: mid-day
    15: 0.72,   # 3 PM: late, high confidence, expensive (TIER 1 entry)
    16: 0.85,   # 4 PM: very late
}

# Strategy-Specific Thresholds
STRATEGY_THRESHOLDS = {
    "TIER1": 95,   # Very conservative
    "TIER2": 70,   # Moderate threshold
    "TIER3": 60,   # More aggressive (for future use)
}

# Confidence Score Components (max points per category)
CONFIDENCE_SCORE_WEIGHTS = {
    "rate_decline": 40,      # Velocity decline (0-40 points)
    "velocity_level": 30,    # How flat is temp rising (0-30 points)
    "tgroup_proximity": 20,  # How close to prediction (0-20 points)
    "flatness": 10,          # Plateau detection (0-10 points)
}

# Safety Gates
SAFETY_GATES = {
    "check_engine_light_threshold": 1.5,  # Model error limit (°F)
    "small_numbers_gate_threshold": 0.5,  # Min V1 for meaningful signal (°F/hr)
    "model_variance_tolerance": 1.5,      # Max model error before abort
}

# Solar Time Multipliers
SOLAR_TIME_MULTIPLIERS = {
    11: 0.2,    # 11 AM: very early, low credibility
    12: 0.5,    # 12 PM: early
    13: 1.0,    # 1 PM: peak credibility
    14: 1.0,    # 2 PM: peak credibility
    15: 0.9,    # 3 PM: good but solar peak passed
    16: 0.5,    # 4 PM: late, less relevant
}

# METAR Data Requirements
METAR_MIN_READINGS = 3  # Need at least 3 readings to calculate acceleration

# Logging Configuration
LOG_DIR = "paper_trading_logs"
LOG_FORMAT = "json"  # json or csv

# Win Condition (simplified for now)
# A trade wins if: actual_settlement < entry_price
# e.g., entry at $0.35 and settlement at $0.32 = WIN (+$0.65 profit)
WIN_CONDITION = "settlement_below_entry"

# Payout Structure
CONTRACT_PAYOUT = 1.00  # Kalshi contracts pay $1.00 if you win
