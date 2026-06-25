"""
Verify Bot Configuration Schema

Defines the Pydantic model for validating Verify bot configuration.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Any, Literal


class SymbolConfig(BaseModel):
    """Configuration for a single symbol."""
    ticker: str = Field(..., description="Symbol ticker (e.g., 'IBM', 'SPX')")
    exchange: str = Field(default="SMART", description="Exchange (e.g., 'SMART', 'CBOE', 'IBIS')")
    currency: str = Field(default="USD", description="Currency (e.g., 'USD', 'EUR')")
    right: str = Field(default="C", description="Option right for options: 'C' for Call, 'P' for Put")
    
    @field_validator('right')
    @classmethod
    def validate_right(cls, v: str) -> str:
        """Validate option right."""
        if v not in ['C', 'P']:
            raise ValueError(f"Option right must be 'C' or 'P', got '{v}'")
        return v


class VerifyBotConfig(BaseModel):
    """
    Configuration schema for the Verify bot.
    
    The Verify bot tests IB connection and retrieves historical data for configured symbols.
    """
    
    type: str = Field(..., description="Bot type identifier (must be 'verify')")
    
    security_type: Literal["stock", "option", "future"] = Field(
        default="stock",
        description="Type of security to verify: 'stock', 'option', or 'future'"
    )
    
    symbols: List[SymbolConfig] = Field(
        ...,
        min_length=1,
        description="List of symbols to verify"
    )
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate that type is 'verify'."""
        if v != 'verify':
            raise ValueError(f"Bot type must be 'verify', got '{v}'")
        return v
    
    model_config = {
        "extra": "forbid",  # Disallow extra fields not defined in schema
        "str_strip_whitespace": True,  # Strip whitespace from strings
    }
