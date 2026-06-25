"""
FKK Bot Configuration Schema

Defines the Pydantic model for validating FKK bot configuration.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional


class FKKBotConfig(BaseModel):
    """
    Configuration schema for the FKK bot.
    
    The FKK bot places 0 DTE bull put spreads on SPX based on market conditions.
    """
    
    type: str = Field(..., description="Bot type identifier (must be 'fkk')")
    
    execution_time: str = Field(
        default="14:13",
        description="Time to start checking conditions in HH:MM format (24-hour)"
    )
    
    timezone: str = Field(
        default="America/New_York",
        description="Timezone for execution time (e.g., 'America/New_York', 'Europe/Berlin')"
    )
    
    condition_timeout_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        description="How long to monitor conditions before giving up (1-60 minutes)"
    )
    
    num_contracts: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of spread contracts to trade (1-100)"
    )
    
    spread_width: int = Field(
        default=10,
        ge=5,
        le=50,
        description="Width of the spread in strike points (5-50), must be a multiple of 5"
    )
    
    max_price_adjustments: int = Field(
        default=4,
        ge=0,
        le=20,
        description="Maximum number of price adjustments if order not filled (0-20)"
    )
    
    target_delta: float = Field(
        default=0.35,
        ge=0.05,
        le=0.50,
        description="Target delta for the short put option (0.05-0.50)"
    )
    
    price_above_open_threshold: float = Field(
        default=0.003,
        ge=0.0,
        le=0.10,
        description="Threshold for price above today's open as a decimal (e.g., 0.003 = 0.3%, 0.005 = 0.5%)"
    )
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate that type is 'fkk'."""
        if v != 'fkk':
            raise ValueError(f"Bot type must be 'fkk', got '{v}'")
        return v
    
    @field_validator('spread_width')
    @classmethod
    def validate_spread_width(cls, v: int) -> int:
        """Validate spread width is a multiple of 5."""
        if v % 5 != 0:
            raise ValueError(f"Spread width must be a multiple of 5, got {v}")
        return v

    @field_validator('execution_time')
    @classmethod
    def validate_execution_time(cls, v: str) -> str:
        """Validate execution time format (HH:MM)."""
        try:
            parts = v.split(':')
            if len(parts) != 2:
                raise ValueError("Must be in HH:MM format")
            
            hour = int(parts[0])
            minute = int(parts[1])
            
            if not (0 <= hour <= 23):
                raise ValueError("Hour must be 0-23")
            if not (0 <= minute <= 59):
                raise ValueError("Minute must be 0-59")
            
            return v
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid execution_time format '{v}': must be HH:MM (e.g., '14:13')") from e
    
    model_config = {
        "extra": "forbid",  # Disallow extra fields not defined in schema
        "str_strip_whitespace": True,  # Strip whitespace from strings
    }
