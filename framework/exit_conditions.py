"""
Exit Condition Evaluators

Defines base classes and concrete implementations for evaluating exit conditions
for open positions.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime
import asyncio
from ib_async import IB, Contract, Option
import logging


class ExitCondition(ABC):
    """
    Abstract base class for exit conditions.
    
    Each condition evaluates whether a specific criterion is met for exiting a position.
    """
    
    def __init__(self, name: str, config: Dict[str, Any], logger: logging.Logger):
        """
        Initialize the exit condition.
        
        Args:
            name: Name of the condition for logging
            config: Configuration dictionary for this condition
            logger: Logger instance
        """
        self.name = name
        self.config = config
        self.logger = logger
    
    @abstractmethod
    async def evaluate(
        self, 
        ib: IB, 
        position_data: Dict[str, Any],
        current_greeks: Dict[str, float]
    ) -> bool:
        """
        Evaluate whether the exit condition is met.
        
        Args:
            ib: IB connection instance
            position_data: Dictionary containing position information
            current_greeks: Current greeks for the position
            
        Returns:
            True if condition is met (should exit), False otherwise
        """
        pass


class PositionDeltaCondition(ExitCondition):
    """
    Position Delta exit condition.
    
    Checks if the position's delta has changed beyond a threshold from its initial value.
    
    Configuration:
        - operator: Comparison operator (">", ">=", "<", "<=")
        - threshold: Delta threshold value
    """
    
    def __init__(self, name: str, config: Dict[str, Any], logger: logging.Logger):
        super().__init__(name, config, logger)
        
        operator = config.get("operator")
        threshold = config.get("threshold")
        
        if operator not in [">", ">=", "<", "<="]:
            raise ValueError(f"Position delta condition '{name}': operator must be one of: >, >=, <, <=")
        
        if threshold is None:
            raise ValueError(f"Position delta condition '{name}': threshold is required")
        
        self.operator: str = operator
        self.threshold: float = threshold
    
    async def evaluate(
        self, 
        ib: IB, 
        position_data: Dict[str, Any],
        current_greeks: Dict[str, float]
    ) -> bool:
        """
        Evaluate if position delta meets the exit condition.
        
        The condition compares the current position delta against the threshold.
        """
        try:
            current_delta = current_greeks.get('delta', 0.0)
            
            self.logger.info(
                f"Condition '{self.name}': Current delta={current_delta:.4f}, "
                f"Threshold={self.threshold:.4f}"
            )
            
            # Evaluate condition based on operator
            if self.operator == ">":
                result = current_delta > self.threshold
            elif self.operator == ">=":
                result = current_delta >= self.threshold
            elif self.operator == "<":
                result = current_delta < self.threshold
            else:  # "<="
                result = current_delta <= self.threshold
            
            self.logger.info(
                f"Condition '{self.name}': {current_delta:.4f} {self.operator} {self.threshold:.4f} = {result}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Condition '{self.name}': Error evaluating position delta: {e}", exc_info=True)
            return False


class ExitConditionEvaluator:
    """
    Evaluates a list of exit conditions for a position.
    
    All conditions must be met for the evaluator to return True.
    """
    
    # Registry of condition types
    CONDITION_TYPES = {
        "position_delta": PositionDeltaCondition,
    }
    
    def __init__(self, conditions_config: List[Dict[str, Any]], logger: logging.Logger):
        """
        Initialize the exit condition evaluator.
        
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
        
        self.logger.info(f"Initialized {len(self.conditions)} exit conditions")
    
    async def evaluate_all(
        self, 
        ib: IB, 
        position_data: Dict[str, Any],
        current_greeks: Dict[str, float]
    ) -> bool:
        """
        Evaluate all exit conditions for a position.
        
        Args:
            ib: IB connection instance
            position_data: Dictionary containing position information
            current_greeks: Current greeks for the position
            
        Returns:
            True if all conditions are met (should exit), False otherwise
        """
        if not self.conditions:
            self.logger.debug("No exit conditions configured")
            return False
        
        self.logger.debug(f"Evaluating {len(self.conditions)} exit conditions")
        
        for condition in self.conditions:
            result = await condition.evaluate(ib, position_data, current_greeks)
            
            if not result:
                self.logger.debug(f"Exit condition '{condition.name}' NOT met")
                return False
        
        self.logger.info("All exit conditions met - triggering position exit")
        return True


async def calculate_position_greeks(
    ib: IB,
    legs_data: Dict[str, Any],
    logger: logging.Logger
) -> Dict[str, float]:
    """
    Calculate greeks for a position based on its legs.
    
    Args:
        ib: IB connection instance
        legs_data: Dictionary containing leg information
        logger: Logger instance
        
    Returns:
        Dictionary with position greeks (delta, gamma, theta, vega)
    """
    try:
        position_greeks = {
            'delta': 0.0,
            'gamma': 0.0,
            'theta': 0.0,
            'vega': 0.0
        }
        
        # Reconstruct option contracts from legs_data
        for leg_name, leg_info in legs_data.items():
            symbol = leg_info['symbol']
            expiration = leg_info['expiration']
            strike = leg_info['strike']
            right = leg_info['right']
            ratio = leg_info['ratio']
            
            # Create option contract
            option = Option(symbol, expiration, strike, right, "SMART", currency="USD")
            
            # Qualify contract
            qualified = await ib.qualifyContractsAsync(option)
            if not qualified or not isinstance(qualified[0], Contract):
                logger.warning(f"Could not qualify option for leg '{leg_name}'")
                continue
            
            contract = qualified[0]
            
            # Request market data with greeks
            ticker = ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(2)  # Wait for greeks to populate
            
            # Extract greeks and apply ratio
            if ticker.modelGreeks:
                greeks = ticker.modelGreeks
                position_greeks['delta'] += (greeks.delta or 0.0) * ratio
                position_greeks['gamma'] += (greeks.gamma or 0.0) * ratio
                position_greeks['theta'] += (greeks.theta or 0.0) * ratio
                position_greeks['vega'] += (greeks.vega or 0.0) * ratio
            
            # Cancel market data
            ib.cancelMktData(contract)
        
        logger.debug(f"Calculated position greeks: {position_greeks}")
        return position_greeks
        
    except Exception as e:
        logger.error(f"Error calculating position greeks: {e}", exc_info=True)
        return {'delta': 0.0, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0}
