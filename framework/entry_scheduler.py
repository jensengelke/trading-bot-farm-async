"""
Exchange-aware entry scheduler.

Manages trading day qualification, fixed schedules, and Phase 1 floating entry windows
in America/New_York timezone. Ensures atomic once-per-day consumption using entry_latches.
"""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple
import pytz

from bots.optionomega.config import EntryConfig
from framework.position_manager import PositionManager

NY_TZ = pytz.timezone("America/New_York")
DAY_ABBRS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class EntryScheduler:
    """Evaluates whether an entry can and should be attempted for a bot."""

    def __init__(self, bot_id: str, entry_config: EntryConfig, position_manager: PositionManager, logger: logging.Logger):
        self.bot_id = bot_id
        self.config = entry_config
        self.position_manager = position_manager
        self.logger = logger

    def get_ny_now(self) -> datetime:
        return datetime.now(NY_TZ)

    def get_ny_date_str(self, dt: Optional[datetime] = None) -> str:
        d = dt or self.get_ny_now()
        return d.strftime("%Y-%m-%d")

    def get_ny_time_str(self, dt: Optional[datetime] = None) -> str:
        d = dt or self.get_ny_now()
        return d.strftime("%H:%M")

    def is_scheduled_day(self, dt: Optional[datetime] = None) -> bool:
        """Check whether dt qualifies under configured frequency and blackout dates."""
        d = dt or self.get_ny_now()
        date_str = self.get_ny_date_str(d)

        # Blackout dates
        if date_str in (self.config.blackout_dates or []):
            return False

        freq = self.config.frequency
        if freq.type == "daily":
            # Monday through Friday
            return d.weekday() < 5
        elif freq.type == "weekly":
            day_name = DAY_ABBRS[d.weekday()]
            return day_name in (freq.days or [])
        elif freq.type == "specific_dates":
            return date_str in (freq.dates or [])
        elif freq.type == "monthly":
            # For simplicity, monthly trading days can be configured with specific dates
            return True
        return False

    def can_attempt_entry(self, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Check if bot is eligible to evaluate conditions and attempt entry.
        Does NOT consume the day latch yet.
        """
        d = dt or self.get_ny_now()
        date_str = self.get_ny_date_str(d)
        time_str = self.get_ny_time_str(d)

        if not self.is_scheduled_day(d):
            return False, f"Date {date_str} is not a scheduled trading day"

        # Check if already latched
        if self.position_manager.is_entry_latched(self.bot_id, date_str):
            return False, f"Entry already consumed/latched for trading date {date_str}"

        floating = self.config.floating_entry
        if floating and floating.enabled:
            if time_str < floating.min_time or time_str > floating.max_time:
                return False, f"Time {time_str} is outside floating window [{floating.min_time}, {floating.max_time}]"
            return True, f"Within floating window [{floating.min_time}, {floating.max_time}]"

        # Fixed entry times
        if self.config.times:
            if time_str in self.config.times:
                return True, f"Matched scheduled time {time_str}"
            return False, f"Time {time_str} does not match any scheduled times {self.config.times}"

        return False, "No valid floating window or fixed entry times configured"

    def consume_latch(self, trigger_reason: str, position_id: Optional[str] = None, dt: Optional[datetime] = None) -> bool:
        """
        Atomically persist entry latch for today BEFORE order strike selection or submission.
        Returns True if latch was successfully acquired, False if already consumed.
        """
        date_str = self.get_ny_date_str(dt)
        latched = self.position_manager.consume_entry_latch(
            self.bot_id, date_str, trigger_reason=trigger_reason, position_id=position_id
        )
        if latched:
            self.logger.info(f"Consumed entry latch for bot {self.bot_id} on {date_str}: {trigger_reason}")
        else:
            self.logger.warning(f"Failed to consume entry latch (already latched) for {self.bot_id} on {date_str}")
        return latched
