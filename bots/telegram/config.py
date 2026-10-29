"""
Telegram Bot Configuration Schema

Defines the Pydantic model for validating Telegram bot configuration.
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class OrderNotificationsConfig(BaseModel):
    """Configuration for real-time order and execution notifications."""
    enabled: bool = Field(default=True, description="Enable real-time order notifications")
    notify_on_placement: bool = Field(default=True, description="Notify when order is submitted")
    notify_on_status_change: bool = Field(default=True, description="Notify on status transitions")
    notify_on_cancellation: bool = Field(default=True, description="Notify on cancellations")
    notify_on_fill: bool = Field(default=True, description="Notify on execution fills")
    notify_on_price_adjustments: bool = Field(
        default=False,
        description="Notify on tick chase price adjustments (can be noisy)"
    )
    filter_accounts: List[str] = Field(
        default_factory=list,
        description="Optional list of IB account numbers to monitor (empty = monitor all accounts)"
    )
    include_order_refs: List[str] = Field(
        default_factory=list,
        description="Optional list of orderRef prefixes or regex to include"
    )
    exclude_order_refs: List[str] = Field(
        default_factory=list,
        description="Optional list of orderRef prefixes to ignore (e.g., 'verify_*')"
    )


class ObservationsConfig(BaseModel):
    """Configuration for scheduled portfolio observations (Phase 2)."""
    enabled: bool = Field(default=False, description="Enable scheduled portfolio observations")
    schedule: Optional[str] = Field(
        default="0 9,15 * * 1-5",
        description="Cron expression for observation schedule"
    )
    timezone: str = Field(default="America/New_York", description="Timezone for schedule")
    rules: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of observation rules to evaluate"
    )


class TelegramBotConfig(BaseModel):
    """Configuration schema for Telegram bot."""
    type: Literal["telegram"] = Field(..., description="Bot type identifier (must be 'telegram')")
    name: Optional[str] = Field(default=None, description="Optional bot display name")
    connection: str = Field(
        default="default",
        description="Telegram connection name configured in .secret-config.yaml"
    )
    silent_startup: bool = Field(
        default=False,
        description="If True, suppress the 'Telegram Notifier Online' startup message"
    )
    notifications: OrderNotificationsConfig = Field(
        default_factory=OrderNotificationsConfig,
        description="Order notification rules"
    )
    observations: Optional[ObservationsConfig] = Field(
        default_factory=ObservationsConfig,
        description="Observation rules configuration"
    )

    model_config = {
        "extra": "forbid",
        "str_strip_whitespace": True,
    }
