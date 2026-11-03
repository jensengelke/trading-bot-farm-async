"""
OptionOmega Bot Configuration Schema.

Strict Pydantic models for OptionOmega live bot configurations.
Rejects any unknown keys (extra: forbid), including backtest_only and slippage settings.
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class UnderlyingConfig(StrictBaseModel):
    ticker: str
    sec_type: str = "IND"
    exchange: str = "SMART"
    currency: str = "USD"


class DeltaStrikeSpec(StrictBaseModel):
    type: Literal["delta"]
    value: float = Field(..., description="Target delta (0 to 100 or -100 to 100)")

    @field_validator("value")
    @classmethod
    def validate_delta(cls, v: float) -> float:
        if abs(v) > 100:
            raise ValueError(f"Delta value must be between -100 and 100, got {v}")
        return v


class OffsetStrikeSpec(StrictBaseModel):
    type: Literal["offset"]
    parent: str = Field(..., description="ID of parent leg to offset from")
    value: float = Field(0.0, description="Strike offset in points relative to parent")
    exact: bool = Field(True, description="Whether offset must match exact strikes")


class UnderlyingOffsetStrikeSpec(StrictBaseModel):
    type: Literal["underlying_offset"]
    value: float = Field(..., description="Strike offset in points relative to underlying price")


class FixedStrikeSpec(StrictBaseModel):
    type: Literal["fixed_strike"]
    value: float = Field(..., description="Fixed strike price")


StrikeSpec = Union[DeltaStrikeSpec, OffsetStrikeSpec, UnderlyingOffsetStrikeSpec, FixedStrikeSpec]


class LegConfig(StrictBaseModel):
    id: str
    action: Literal["buy", "sell"]
    right: Literal["call", "put"]
    qty: int = Field(1, ge=1)
    dte: int = Field(..., ge=0)
    strike: StrikeSpec


class AllocationConfig(StrictBaseModel):
    mode: Literal["fixed_quantity"] = "fixed_quantity"
    quantity: int = Field(1, ge=1)
    max_open_positions: int = Field(1, ge=1)


class FrequencyConfig(StrictBaseModel):
    type: Literal["weekly", "daily", "monthly", "specific_dates"]
    days: Optional[List[str]] = None
    dates: Optional[List[str]] = None


class FloatingEntryConfig(StrictBaseModel):
    enabled: bool = False
    min_time: str = "10:00"
    max_time: str = "15:00"

    @model_validator(mode="after")
    def validate_times(self) -> "FloatingEntryConfig":
        if self.enabled and self.min_time >= self.max_time:
            raise ValueError(f"min_time ({self.min_time}) must be earlier than max_time ({self.max_time})")
        return self


class QuoteFilterConfig(StrictBaseModel):
    max_quote_age_seconds: float = Field(5.0, gt=0)
    max_leg_bid_ask_width: float = Field(0.50, gt=0)


class EntryExecutionConfig(StrictBaseModel):
    order_type: Literal["limit"] = "limit"
    mid_sampling_seconds: float = Field(10.0, ge=0)
    starting_offset: float = Field(0.0, ge=0)
    price_increment: float = Field(0.01, gt=0)
    wait_seconds: float = Field(20.0, gt=0)
    max_attempts: int = Field(5, ge=1)
    max_adverse_price_change: float = Field(0.05, ge=0)


class ConditionConfig(StrictBaseModel):
    type: str
    direction: Optional[str] = None
    unit: Optional[str] = None
    threshold: Optional[float] = None
    period: Optional[int] = None
    timeframe: Optional[str] = None


class EntryConfig(StrictBaseModel):
    frequency: FrequencyConfig
    blackout_dates: List[str] = Field(default_factory=list)
    times: List[str] = Field(default_factory=list)
    floating_entry: Optional[FloatingEntryConfig] = None
    conditions: List[ConditionConfig] = Field(default_factory=list)
    min_credit_for_open: Optional[float] = Field(None, ge=0)
    max_credit_for_open: Optional[float] = Field(None, ge=0)
    min_debit_for_open: Optional[float] = Field(None, ge=0)
    max_debit_for_open: Optional[float] = Field(None, ge=0)
    option_omega_min_entry_premium: Optional[float] = Field(None, ge=0)
    option_omega_max_entry_premium: Optional[float] = Field(None, ge=0)
    quote_filter: Optional[QuoteFilterConfig] = None
    execution: EntryExecutionConfig

    @model_validator(mode="after")
    def validate_premium_filters(self) -> "EntryConfig":
        has_explicit = any(
            b is not None
            for b in (
                self.min_credit_for_open,
                self.max_credit_for_open,
                self.min_debit_for_open,
                self.max_debit_for_open,
            )
        )
        has_oo = any(
            b is not None
            for b in (
                self.option_omega_min_entry_premium,
                self.option_omega_max_entry_premium,
            )
        )
        if has_explicit and has_oo:
            raise ValueError(
                "Both explicit entry premium bounds and OptionOmega bounds are non-null; choose only one family"
            )
        if self.min_credit_for_open is not None and self.max_credit_for_open is not None:
            if self.min_credit_for_open > self.max_credit_for_open:
                raise ValueError("min_credit_for_open cannot exceed max_credit_for_open")
        if self.min_debit_for_open is not None and self.max_debit_for_open is not None:
            if self.min_debit_for_open > self.max_debit_for_open:
                raise ValueError("min_debit_for_open cannot exceed max_debit_for_open")
        if self.option_omega_min_entry_premium is not None and self.option_omega_max_entry_premium is not None:
            if self.option_omega_min_entry_premium > self.option_omega_max_entry_premium:
                raise ValueError("option_omega_min_entry_premium cannot exceed option_omega_max_entry_premium")

        # Floating entry constraints
        if self.floating_entry and self.floating_entry.enabled:
            if len(self.times) > 1:
                raise ValueError("Floating entry cannot be combined with multiple fixed entry times")

        return self



class ProfitTargetConfig(StrictBaseModel):
    type: Literal["percent", "fixed_premium", "closing_premium"]
    value: float = Field(..., gt=0)


class StopLossConfig(StrictBaseModel):
    type: Literal["percent", "fixed_premium", "closing_premium"]
    value: float = Field(..., gt=0)
    order_type: Literal["stop", "stop_limit"] = "stop"
    trigger_method: str = "default"
    stop_limit_offset: Optional[float] = None

    @model_validator(mode="after")
    def validate_stop_limit(self) -> "StopLossConfig":
        if self.order_type == "stop_limit" and self.stop_limit_offset is None:
            raise ValueError("stop_limit_offset is required when stop loss order_type is stop_limit")
        return self


class ExitMonitoringConfig(StrictBaseModel):
    interval_seconds: float = Field(60.0, gt=0)
    align_to_minute: bool = True


class ExitExecutionConfig(StrictBaseModel):
    price_increment: float = Field(0.01, gt=0)
    wait_seconds: float = Field(20.0, gt=0)
    max_attempts: int = Field(5, ge=1)


class ExitConfig(StrictBaseModel):
    close_full_position_before_first_leg_expires: bool
    close_full_position_before_first_leg_expires_minutes: Optional[int] = Field(None, ge=1)
    managed_exits: List[Dict[str, Any]] = Field(default_factory=list)
    monitoring: ExitMonitoringConfig = Field(default_factory=ExitMonitoringConfig)
    execution: Optional[ExitExecutionConfig] = None

    @model_validator(mode="after")
    def validate_expiration_policy(self) -> "ExitConfig":
        if self.close_full_position_before_first_leg_expires:
            if self.close_full_position_before_first_leg_expires_minutes is None:
                raise ValueError(
                    "close_full_position_before_first_leg_expires_minutes is required when "
                    "close_full_position_before_first_leg_expires is true"
                )
            if self.execution is None:
                raise ValueError("exit.execution parameters are required when first leg expiration close is enabled")
        return self


class OptionOmegaBotConfig(StrictBaseModel):
    type: Literal["optionomega"]
    name: str
    test_mode: bool = False
    underlying: UnderlyingConfig
    legs: List[LegConfig] = Field(..., min_length=1)
    use_exact_dte: bool = False
    round_strikes_to_nearest: Optional[float] = Field(None, gt=0)
    allocation: AllocationConfig = Field(default_factory=AllocationConfig)
    entry: EntryConfig
    profit_target: Optional[ProfitTargetConfig] = None
    stop_loss: Optional[StopLossConfig] = None
    exit: ExitConfig

    @model_validator(mode="after")
    def validate_legs_and_exits(self) -> "OptionOmegaBotConfig":
        # 1. Unique leg IDs
        leg_ids = [leg.id for leg in self.legs]
        if len(leg_ids) != len(set(leg_ids)):
            raise ValueError(f"Leg IDs must be unique, found duplicates in: {leg_ids}")

        # 2. Acyclic parent links
        id_set = set(leg_ids)
        for leg in self.legs:
            if isinstance(leg.strike, OffsetStrikeSpec):
                parent_id = leg.strike.parent
                if parent_id not in id_set:
                    raise ValueError(f"Leg '{leg.id}' references non-existent parent leg '{parent_id}'")
                if parent_id == leg.id:
                    raise ValueError(f"Leg '{leg.id}' cannot reference itself as parent")

        # Detect cycle in parent links
        parent_map = {
            leg.id: leg.strike.parent
            for leg in self.legs
            if isinstance(leg.strike, OffsetStrikeSpec)
        }
        for start_id in parent_map:
            visited = set()
            curr = start_id
            while curr in parent_map:
                if curr in visited:
                    raise ValueError(f"Cycle detected in leg strike parent references involving leg '{curr}'")
                visited.add(curr)
                curr = parent_map[curr]

        return self

