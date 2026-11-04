"""
DAX Spread Observation Bot Configuration Schema.

Defines Pydantic model for validating the DAX Spread Observation bot configuration.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class DaxSpreadObservationConfig(BaseModel):
    """Configuration schema for DAX spread observation bot."""
    type: Literal["dax_spread_observation"] = Field(..., description="Bot type identifier")
    name: Optional[str] = Field(default="DAX Spread Observer", description="Optional bot display name")
    connection: str = Field(default="default", description="Telegram connection name from .secret-config.yaml")
    account: Optional[str] = Field(default=None, description="Account ID to monitor (defaults to system selected_account)")
    
    # Underlying specification
    symbol: str = Field(default="DAX", description="Option underlying symbol")
    exchange: str = Field(default="EUREX", description="Exchange for options and underlying index")
    currency: str = Field(default="EUR", description="Currency for contracts")
    market_data_type: int = Field(default=3, description="IB market data type: 1=Live, 2=Frozen, 3=Delayed, 4=Delayed Frozen")
    
    # Risk thresholds
    max_short_delta: float = Field(default=0.38, description="Delta threshold for short leg alerts (absolute value)")
    strike_tested_buffer_points: float = Field(default=50.0, description="Buffer in points to trigger short strike tested alert")
    
    # Scheduling & Intervals
    monitoring_interval_seconds: float = Field(default=60.0, description="Interval in seconds between risk evaluations")
    daily_summary_schedule: List[str] = Field(
        default_factory=lambda: ["09:15", "17:45"],
        description="Daily times (HH:MM 24-hr format) to broadcast portfolio summary"
    )
    timezone: str = Field(default="Europe/Berlin", description="Timezone for evaluation and summaries")
    alert_cooldown_minutes: float = Field(default=60.0, description="Cooldown in minutes before repeating identical breach alert")

    model_config = {
        "extra": "forbid",
        "str_strip_whitespace": True,
    }
