"""
Entry Condition Evaluators

Defines base classes and concrete implementations for evaluating entry conditions
for trading strategies.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime
import asyncio
from ib_async import IB, Contract
import logging


class EntryCondition(ABC):
    """
    Abstract base class for entry conditions.
    
    Each condition evaluates whether a specific criterion is met for entering a trade.
    """
    
    def __init__(self, name: str, config: Dict[str, Any], logger: logging.Logger):
        """
        Initialize the entry condition.
        
        Args:
            name: Name of the condition for logging
            config: Configuration dictionary for this condition
            logger: Logger instance
        """
        self.name = name
        self.config = config
        self.logger = logger
    
    @abstractmethod
    async def evaluate(self, ib: IB, underlying: Contract, current_price: float) -> bool:
        """
        Evaluate whether the condition is met.
        
        Args:
            ib: IB connection instance
            underlying: Qualified underlying contract
            current_price: Current price of the underlying
            
        Returns:
            True if condition is met, False otherwise
        """
        pass


class SMACondition(EntryCondition):
    """
    Simple Moving Average condition.
    
    Checks if current price is above/below/equal to an SMA of specified period.
    
    Configuration:
        - period: Number of days for SMA calculation (e.g., 5 for 5-day SMA)
        - operator: Comparison operator (">", ">=", "<", "<=", "==")
    """
    
    def __init__(self, name: str, config: Dict[str, Any], logger: logging.Logger):
        super().__init__(name, config, logger)
        
        period = config.get("period")
        operator = config.get("operator")
        
        if not period or period < 1:
            raise ValueError(f"SMA condition '{name}': period must be >= 1")
        
        if operator not in [">", ">=", "<", "<=", "=="]:
            raise ValueError(f"SMA condition '{name}': operator must be one of: >, >=, <, <=, ==")
        
        self.period: int = period
        self.operator: str = operator
    
    async def evaluate(self, ib: IB, underlying: Contract, current_price: float) -> bool:
        """
        Evaluate if current price meets the SMA condition.
        
        The SMA is calculated as: (sum of last (period-1) daily closes + current price) / period
        """
        try:
            # Get last (period-1) days of daily bars
            bars = await ib.reqHistoricalDataAsync(
                underlying,
                endDateTime="",
                durationStr=f"{self.period} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True
            )
            
            if len(bars) < (self.period - 1):
                self.logger.warning(
                    f"Condition '{self.name}': Not enough historical data "
                    f"(got {len(bars)} bars, need {self.period - 1})"
                )
                return False
            
            # Calculate SMA: sum of last (period-1) closes + current price, divided by period
            last_closes = [bar.close for bar in bars[-(self.period - 1):]]
            sma = (sum(last_closes) + current_price) / self.period
            
            self.logger.info(
                f"Condition '{self.name}': SMA{self.period}={sma:.2f}, "
                f"Current price={current_price:.2f}"
            )
            
            # Evaluate condition based on operator
            if self.operator == ">":
                result = current_price > sma
            elif self.operator == ">=":
                result = current_price >= sma
            elif self.operator == "<":
                result = current_price < sma
            elif self.operator == "<=":
                result = current_price <= sma
            else:  # "=="
                result = abs(current_price - sma) < 0.01  # Allow small tolerance for equality
            
            self.logger.info(
                f"Condition '{self.name}': {current_price:.2f} {self.operator} {sma:.2f} = {result}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Condition '{self.name}': Error evaluating SMA: {e}", exc_info=True)
            return False


class UnderlyingIntradayMoveCondition(EntryCondition):
    """
    Intraday move condition.
    
    Checks if current price has moved by a certain percentage from today's open.
    
    Configuration:
        - threshold: Percentage threshold as decimal (e.g., 0.003 for 0.3%)
        - operator: Comparison operator (">", ">=", "<", "<=")
    """
    
    def __init__(self, name: str, config: Dict[str, Any], logger: logging.Logger):
        super().__init__(name, config, logger)
        
        threshold = config.get("threshold")
        operator = config.get("operator")
        
        if threshold is None:
            raise ValueError(f"Intraday move condition '{name}': threshold is required")
        
        if operator not in [">", ">=", "<", "<="]:
            raise ValueError(f"Intraday move condition '{name}': operator must be one of: >, >=, <, <=")
        
        self.threshold: float = threshold
        self.operator: str = operator
    
    async def evaluate(self, ib: IB, underlying: Contract, current_price: float) -> bool:
        """
        Evaluate if current price has moved by threshold percentage from today's open.
        """
        try:
            # Get today's bar
            bars = await ib.reqHistoricalDataAsync(
                underlying,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True
            )
            
            if not bars:
                self.logger.warning(f"Condition '{self.name}': Could not get today's open price")
                return False
            
            today_open = bars[-1].open
            
            # Calculate the threshold price
            threshold_price = today_open * (1 + self.threshold)
            
            # Calculate actual move percentage
            actual_move_pct = (current_price - today_open) / today_open
            
            self.logger.info(
                f"Condition '{self.name}': Today's open={today_open:.2f}, "
                f"Current={current_price:.2f}, "
                f"Move={actual_move_pct*100:.2f}%, "
                f"Threshold={self.threshold*100:.2f}%"
            )
            
            # Evaluate condition based on operator
            if self.operator == ">":
                result = current_price > threshold_price
            elif self.operator == ">=":
                result = current_price >= threshold_price
            elif self.operator == "<":
                result = current_price < threshold_price
            else:  # "<="
                result = current_price <= threshold_price
            
            self.logger.info(
                f"Condition '{self.name}': {current_price:.2f} {self.operator} {threshold_price:.2f} = {result}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(
                f"Condition '{self.name}': Error evaluating intraday move: {e}",
                exc_info=True
            )
            return False


class VIXCondition(EntryCondition):
    """
    VIX level condition.
    
    Checks if VIX index meets the specified threshold.
    
    Configuration:
        - threshold: VIX value (e.g., 15)
        - operator: Comparison operator (">", ">=", "<", "<=")
    """
    
    def __init__(self, name: str, config: Dict[str, Any], logger: logging.Logger):
        super().__init__(name, config, logger)
        
        threshold = config.get("threshold")
        operator = config.get("operator")
        
        if threshold is None:
            raise ValueError(f"VIX condition '{name}': threshold is required")
        
        if operator not in [">", ">=", "<", "<="]:
            raise ValueError(f"VIX condition '{name}': operator must be one of: >, >=, <, <=")
        
        self.threshold: float = threshold
        self.operator: str = operator
    
    async def evaluate(self, ib: IB, underlying: Contract, current_price: float) -> bool:
        """
        Evaluate if current VIX level meets the threshold.
        """
        import math
        from ib_async import Index
        
        try:
            # Create VIX index contract
            vix_contract = Index('VIX', 'CBOE', 'USD')
            qualified = await ib.qualifyContractsAsync(vix_contract)
            
            if not qualified or not isinstance(qualified[0], Contract):
                self.logger.error(f"Condition '{self.name}': Failed to qualify VIX contract")
                return False
                
            vix = qualified[0]
            
            ticker = ib.reqMktData(vix, "", False, False)
            
            # Wait for market data with retries
            for _ in range(5):
                await asyncio.sleep(1)
                # Check for valid price
                if ticker.last and ticker.last > 0 and not math.isnan(ticker.last):
                    break
                elif ticker.close and ticker.close > 0 and not math.isnan(ticker.close):
                    ticker.last = ticker.close
                    break
            
            if not ticker.last or ticker.last <= 0 or math.isnan(ticker.last):
                self.logger.warning(
                    f"Condition '{self.name}': Could not get valid VIX price: "
                    f"last={ticker.last}, close={ticker.close}"
                )
                ib.cancelMktData(vix)
                return False
                
            vix_price = ticker.last
            ib.cancelMktData(vix)
            
            self.logger.info(
                f"Condition '{self.name}': VIX={vix_price:.2f}, "
                f"Threshold={self.threshold:.2f}"
            )
            
            # Evaluate condition based on operator
            if self.operator == ">":
                result = vix_price > self.threshold
            elif self.operator == ">=":
                result = vix_price >= self.threshold
            elif self.operator == "<":
                result = vix_price < self.threshold
            else:  # "<="
                result = vix_price <= self.threshold
            
            self.logger.info(
                f"Condition '{self.name}': {vix_price:.2f} {self.operator} {self.threshold:.2f} = {result}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(
                f"Condition '{self.name}': Error evaluating VIX: {e}",
                exc_info=True
            )
            return False


class EntryConditionEvaluator:
    """
    Evaluates a list of entry conditions.
    
    All conditions must be met for the evaluator to return True.
    """
    
    # Registry of condition types
    CONDITION_TYPES = {
        "SMA": SMACondition,
        "underlying_intraday_move": UnderlyingIntradayMoveCondition,
        "VIX": VIXCondition,
    }
    
    def __init__(self, conditions_config: list, logger: logging.Logger):
        """
        Initialize the entry condition evaluator.
        
        Args:
            conditions_config: List of condition configurations
            logger: Logger instance
        """
        self.logger = logger
        self.conditions = []
        
        # Create condition instances
        for condition_config in conditions_config:
            condition_name = condition_config.get("name", "unnamed")
            condition_type = condition_config.get("type")
            
            if not condition_type:
                raise ValueError(f"Condition '{condition_name}': type is required")
            
            if condition_type not in self.CONDITION_TYPES:
                raise ValueError(
                    f"Condition '{condition_name}': unknown type '{condition_type}'. "
                    f"Available types: {', '.join(self.CONDITION_TYPES.keys())}"
                )
            
            # Create condition instance
            condition_class = self.CONDITION_TYPES[condition_type]
            condition = condition_class(condition_name, condition_config, logger)
            self.conditions.append(condition)
        
        self.logger.info(f"Initialized {len(self.conditions)} entry conditions")
    
    async def evaluate_all(self, ib: IB, underlying: Contract, current_price: float) -> bool:
        """
        Evaluate all conditions.
        
        Args:
            ib: IB connection instance
            underlying: Qualified underlying contract
            current_price: Current price of the underlying
            
        Returns:
            True if all conditions are met, False otherwise
        """
        if not self.conditions:
            self.logger.info("No entry conditions configured - allowing entry")
            return True
        
        self.logger.info(f"Evaluating {len(self.conditions)} entry conditions")
        
        for condition in self.conditions:
            result = await condition.evaluate(ib, underlying, current_price)
            
            if not result:
                self.logger.info(f"Entry condition '{condition.name}' NOT met - entry blocked")
                return False
        
        self.logger.info("All entry conditions met - allowing entry")
        return True
