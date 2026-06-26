"""
Butterfly Bot Configuration Schema

Defines the Pydantic model for validating butterfly bot configuration.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal


class ButterflyLegConfig(BaseModel):
    """
    Configuration for a single leg of the butterfly spread.
    """
    
    name: str = Field(..., description="Name of the leg for logging and reference")
    
    right: Literal["call", "put"] = Field(..., description="Option right: 'call' or 'put'")
    
    ratio: int = Field(..., description="Number of contracts (positive for buy, negative for sell)")
    
    strike_selection: Literal["underlying_offset", "leg_offset"] = Field(
        ...,
        description="How to select strike: 'underlying_offset' (relative to underlying price) or 'leg_offset' (relative to another leg)"
    )
    
    strike_offset: float = Field(
        ...,
        description="Offset in points for strike selection (can be positive or negative)"
    )
    
    strike_selection_parent: Optional[str] = Field(
        default=None,
        description="Name of parent leg when using 'leg_offset' strike selection"
    )
    
    @field_validator('strike_selection_parent')
    @classmethod
    def validate_parent(cls, v: Optional[str], info) -> Optional[str]:
        """Validate that parent is specified when using leg_offset."""
        if info.data.get('strike_selection') == 'leg_offset' and not v:
            raise ValueError("strike_selection_parent is required when strike_selection is 'leg_offset'")
        if info.data.get('strike_selection') == 'underlying_offset' and v:
            raise ValueError("strike_selection_parent should not be specified when strike_selection is 'underlying_offset'")
        return v


class ButterflyBotConfig(BaseModel):
    """
    Configuration schema for the Butterfly bot.
    
    The Butterfly bot places configurable butterfly spreads on various underlyings
    at scheduled times.
    """
    
    type: str = Field(..., description="Bot type identifier (must be 'butterfly')")
    
    # Underlying configuration
    underlying_type: Literal["Index", "Stock"] = Field(
        ...,
        description="Type of underlying: 'Index' or 'Stock'"
    )
    
    underlying_symbol: str = Field(
        ...,
        description="Symbol of the underlying (e.g., 'SPX', 'AAPL')"
    )
    
    underlying_exchange: str = Field(
        default="SMART",
        description="Exchange for the underlying (e.g., 'CBOE', 'SMART')"
    )
    
    # Expiration configuration
    dte: int = Field(
        ...,
        ge=0,
        le=365,
        description="Days to expiration (0-365)"
    )
    
    use_exact_dte: bool = Field(
        default=False,
        description="If true, only use exact DTE. If false, search for closest available expiration."
    )
    
    # Scheduling configuration
    entry_days: List[Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]] = Field(
        ...,
        min_length=1,
        description="Days of the week when trades can be entered"
    )
    
    entry_times: List[str] = Field(
        ...,
        min_length=1,
        description="Times in HH:MM format when trades can be entered"
    )
    
    timezone: str = Field(
        default="America/New_York",
        description="Timezone for entry times (e.g., 'America/New_York', 'Europe/Berlin')"
    )
    
    # Leg configuration
    legs: List[ButterflyLegConfig] = Field(
        ...,
        min_length=2,
        description="List of legs that make up the butterfly spread"
    )
    
    # Position sizing
    num_contracts: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of butterfly spreads to trade (1-100)"
    )
    
    # Pricing configuration
    mid_price_monitoring_period: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Time in seconds to monitor mid prices before placing order (1-300)"
    )
    
    min_premium: Optional[float] = Field(
        default=None,
        description="Minimum premium (can be negative for credit or positive for debit)"
    )
    
    max_premium: Optional[float] = Field(
        default=None,
        description="Maximum premium (can be negative for credit or positive for debit)"
    )
    
    max_price_adjustments: int = Field(
        default=4,
        ge=0,
        le=20,
        description="Maximum number of price adjustments if order not filled (0-20)"
    )
    
    # Bracket orders
    stoploss_factor: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Stop loss factor (multiplier of initial premium)"
    )
    
    takeprofit_factor: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Take profit factor (multiplier of initial premium)"
    )
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate that type is 'butterfly'."""
        if v != 'butterfly':
            raise ValueError(f"Bot type must be 'butterfly', got '{v}'")
        return v
    
    @field_validator('entry_times')
    @classmethod
    def validate_entry_times(cls, v: List[str]) -> List[str]:
        """Validate entry time format (HH:MM)."""
        for time_str in v:
            try:
                parts = time_str.split(':')
                if len(parts) != 2:
                    raise ValueError("Must be in HH:MM format")
                
                hour = int(parts[0])
                minute = int(parts[1])
                
                if not (0 <= hour <= 23):
                    raise ValueError("Hour must be 0-23")
                if not (0 <= minute <= 59):
                    raise ValueError("Minute must be 0-59")
            except (ValueError, AttributeError) as e:
                raise ValueError(f"Invalid entry_time format '{time_str}': must be HH:MM (e.g., '14:13')") from e
        return v
    
    @field_validator('legs')
    @classmethod
    def validate_legs(cls, v: List[ButterflyLegConfig]) -> List[ButterflyLegConfig]:
        """Validate leg configuration consistency."""
        # Check that all leg names are unique
        names = [leg.name for leg in v]
        if len(names) != len(set(names)):
            raise ValueError("All leg names must be unique")
        
        # Check that parent legs exist
        for leg in v:
            if leg.strike_selection == 'leg_offset' and leg.strike_selection_parent:
                if leg.strike_selection_parent not in names:
                    raise ValueError(f"Parent leg '{leg.strike_selection_parent}' not found for leg '{leg.name}'")
                if leg.strike_selection_parent == leg.name:
                    raise ValueError(f"Leg '{leg.name}' cannot reference itself as parent")
        
        # Check that all legs have the same right (all calls or all puts)
        rights = [leg.right for leg in v]
        if len(set(rights)) > 1:
            raise ValueError("All legs must have the same right (all 'call' or all 'put')")
        
        return v
    
    @field_validator('min_premium', 'max_premium')
    @classmethod
    def validate_premium_range(cls, v: Optional[float], info) -> Optional[float]:
        """Validate premium range."""
        if v is None:
            return v
        
        # Check that min <= max if both are specified
        if info.field_name == 'max_premium':
            min_premium = info.data.get('min_premium')
            if min_premium is not None and v < min_premium:
                raise ValueError(f"max_premium ({v}) must be >= min_premium ({min_premium})")
        
        return v
    
    model_config = {
        "extra": "forbid",  # Disallow extra fields not defined in schema
        "str_strip_whitespace": True,  # Strip whitespace from strings
    }
