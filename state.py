"""
state.py — DailyState dataclass and StateManager singleton.

DailyState holds everything the bot knows about a city for a single trading day.
StateManager wraps all three city states and handles midnight reset + JSON persistence.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

from config import CITIES

logger = logging.getLogger(__name__)

# Where state JSON files are stored
STATE_DIR = Path(os.getenv("STATE_DIR", "state"))


# ---------------------------------------------------------------------------
# DailyState
# ---------------------------------------------------------------------------

@dataclass
class DailyState:
    """All per-city data for one trading day."""

    station: str
    date: date   # The EST trading date this state covers

    # METAR readings accumulated across the day: list of (utc_datetime, temp_f)
    metar_readings: list = field(default_factory=list)   # list[tuple[datetime, float]]

    # Highest temp seen so far and when it occurred (UTC)
    suspected_high: Optional[float] = None
    suspected_high_time: Optional[datetime] = None

    # First reading lower than suspected_high
    drop_detected: bool = False
    drop_temp: Optional[float] = None
    drop_time: Optional[datetime] = None

    # DSM official confirmation
    dsm_confirmed: bool = False
    dsm_max_temp: Optional[float] = None
    dsm_issued_time: Optional[datetime] = None

    # Whether alert/timeout messages have fired
    drop_alert_fired: bool = False     # Afternoon "peak detected" notification
    alert_fired: bool = False          # Evening "CLI confirmed" notification
    dsm_timeout_fired: bool = False

    # Kalshi market data (populated after DSM confirmation)
    kalshi_ticker: Optional[str] = None
    kalshi_bracket_low: Optional[float] = None
    kalshi_bracket_high: Optional[float] = None
    kalshi_price: Optional[float] = None        # 0.0–1.0 dollar value
    kalshi_close_time: Optional[datetime] = None

    # Running error log: list of (source, utc_datetime, message)
    error_log: list = field(default_factory=list)  # list[tuple[str, datetime, str]]

    # Track how many DSM poll attempts were made before confirmation
    dsm_hold_count: int = 0

    def log_error(self, source: str, message: str) -> None:
        self.error_log.append((source, datetime.utcnow(), message))
        logger.warning("[%s] %s: %s", self.station, source, message)

    def prune_errors(self, max_age_minutes: int = 30) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
        self.error_log = [(s, t, m) for s, t, m in self.error_log if t > cutoff]

    # ------------------------------------------------------------------
    # JSON serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        def _dt(v: Optional[datetime]) -> Optional[str]:
            return v.isoformat() if v else None

        return {
            "station": self.station,
            "date": self.date.isoformat(),
            "metar_readings": [
                [dt.isoformat(), temp] for dt, temp in self.metar_readings
            ],
            "suspected_high": self.suspected_high,
            "suspected_high_time": _dt(self.suspected_high_time),
            "drop_detected": self.drop_detected,
            "drop_temp": self.drop_temp,
            "drop_time": _dt(self.drop_time),
            "dsm_confirmed": self.dsm_confirmed,
            "dsm_max_temp": self.dsm_max_temp,
            "dsm_issued_time": _dt(self.dsm_issued_time),
            "drop_alert_fired": self.drop_alert_fired,
            "alert_fired": self.alert_fired,
            "dsm_timeout_fired": self.dsm_timeout_fired,
            "kalshi_ticker": self.kalshi_ticker,
            "kalshi_bracket_low": self.kalshi_bracket_low,
            "kalshi_bracket_high": self.kalshi_bracket_high,
            "kalshi_price": self.kalshi_price,
            "kalshi_close_time": _dt(self.kalshi_close_time),
            "error_log": [
                [s, t.isoformat(), m] for s, t, m in self.error_log
            ],
            "dsm_hold_count": self.dsm_hold_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DailyState":
        def _dt(v: Optional[str]) -> Optional[datetime]:
            return datetime.fromisoformat(v) if v else None

        state = cls(
            station=d["station"],
            date=date.fromisoformat(d["date"]),
        )
        state.metar_readings = [
            (datetime.fromisoformat(dt_str), temp)
            for dt_str, temp in d.get("metar_readings", [])
        ]
        state.suspected_high = d.get("suspected_high")
        state.suspected_high_time = _dt(d.get("suspected_high_time"))
        state.drop_detected = d.get("drop_detected", False)
        state.drop_temp = d.get("drop_temp")
        state.drop_time = _dt(d.get("drop_time"))
        state.dsm_confirmed = d.get("dsm_confirmed", False)
        state.dsm_max_temp = d.get("dsm_max_temp")
        state.dsm_issued_time = _dt(d.get("dsm_issued_time"))
        state.drop_alert_fired = d.get("drop_alert_fired", False)
        state.alert_fired = d.get("alert_fired", False)
        state.dsm_timeout_fired = d.get("dsm_timeout_fired", False)
        state.kalshi_ticker = d.get("kalshi_ticker")
        state.kalshi_bracket_low = d.get("kalshi_bracket_low")
        state.kalshi_bracket_high = d.get("kalshi_bracket_high")
        state.kalshi_price = d.get("kalshi_price")
        state.kalshi_close_time = _dt(d.get("kalshi_close_time"))
        state.error_log = [
            (s, datetime.fromisoformat(t), m)
            for s, t, m in d.get("error_log", [])
        ]
        state.dsm_hold_count = d.get("dsm_hold_count", 0)
        return state


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------

class StateManager:
    """
    Holds one DailyState per city.  Automatically resets a city's state when
    the EST trading date changes.  Persists state to/from JSON files so the bot
    can survive a server reboot mid-day.
    """

    def __init__(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, DailyState] = {}
        self._est = pytz.timezone("America/New_York")
        self._init_all()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, station: str) -> DailyState:
        """Return the current DailyState for *station*, auto-resetting if stale."""
        self._check_reset(station)
        return self._states[station]

    def save(self, station: str) -> None:
        """Persist the current state for *station* to disk."""
        state = self._states.get(station)
        if state is None:
            return
        path = self._state_path(station, state.date)
        try:
            path.write_text(json.dumps(state.to_dict(), indent=2))
        except Exception as exc:
            logger.error("Failed to persist state for %s: %s", station, exc)

    def save_all(self) -> None:
        for station in CITIES:
            self.save(station)

    def reset_all(self) -> None:
        """Called at midnight EST to start a fresh day.  Deletes yesterday's files."""
        self._init_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _today_est(self) -> date:
        return datetime.now(self._est).date()

    def _state_path(self, station: str, d: date) -> Path:
        return STATE_DIR / f"state_{station}_{d.isoformat()}.json"

    def _init_all(self) -> None:
        today = self._today_est()
        for station in CITIES:
            self._states[station] = self._load_or_create(station, today)

    def _load_or_create(self, station: str, today: date) -> DailyState:
        path = self._state_path(station, today)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                state = DailyState.from_dict(data)
                logger.info("Loaded persisted state for %s from %s", station, path)
                return state
            except Exception as exc:
                logger.warning(
                    "Could not load state file %s (%s) — starting fresh", path, exc
                )
        return DailyState(station=station, date=today)

    def _check_reset(self, station: str) -> None:
        today = self._today_est()
        current = self._states.get(station)
        if current is None or current.date != today:
            # Clean up old state file(s) for this station
            self._cleanup_old_files(station, today)
            self._states[station] = DailyState(station=station, date=today)
            logger.info("State reset for %s — new trading day %s", station, today)

    def _cleanup_old_files(self, station: str, today: date) -> None:
        """Remove state files older than today for *station*."""
        for path in STATE_DIR.glob(f"state_{station}_*.json"):
            try:
                file_date = date.fromisoformat(path.stem.split("_")[-1])
                if file_date < today:
                    path.unlink()
                    logger.info("Deleted old state file: %s", path)
            except ValueError:
                pass  # Filename doesn't match expected pattern, leave it
