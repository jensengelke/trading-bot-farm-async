import sys
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, time, timedelta
import asyncio

# Add parent directory to path to import framework modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ib_async import IB, Index, Stock, Option, Contract, Order, Trade, ComboLeg
from ib_async.order import LimitOrder, StopOrder
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from framework.option_utils import find_option_by_delta
import pytz

# Import bot config schema
try:
    from bots.strategy.config import StrategyBotConfig, StrategyLegConfig
except ImportError:
    # Fallback if running from different context
    from .config import StrategyBotConfig, StrategyLegConfig


@trace_all_methods
class Bot(BotBase):
    """
    Strategy Bot Implementation.
    
    Places configurable option spreads on various underlyings at scheduled times.
    Supports flexible leg configuration, strike selection, and bracket orders.
    """
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager):
        """
        Initialize the Strategy bot.
        
        Args:
            bot_id: Unique identifier for this bot instance
            config: Bot-specific configuration (raw dict)
            system_config: System-wide configuration
            ib_connection_manager: Shared IB connection manager
        """
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        # Validate configuration using Pydantic model
        try:
            self.validated_config = StrategyBotConfig(**config)
            self.logger.info("Configuration validated successfully")
        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}", exc_info=True)
            raise ValueError(f"Invalid configuration for bot {bot_id}: {e}") from e
        
        self.ib: Optional[IB] = None
        self._stop_requested = False
        self._active_trades: List[Trade] = []
        
    async def start(self) -> None:
        """
        Start the strategy bot.
        
        Runs continuously, executing the trading logic at scheduled times.
        """
        self.logger.info("Starting Strategy bot")
        
        # Get connection parameters from system config
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        
        # Get shared IB connection
        try:
            self.ib = await self.ib_connection_manager.connect(host, port, client_id)
            self.logger.info("Using shared IB connection")
        except Exception as e:
            self.logger.error(f"Failed to get IB connection: {e}", exc_info=True)
            raise
        
        # Main loop - run until stop is requested
        while not self._stop_requested:
            try:
                await self._run_scheduled_cycle()
            except Exception as e:
                self.logger.error(f"Error in scheduled cycle: {e}", exc_info=True)
                # Wait before retrying
                await asyncio.sleep(60)
    
    async def _run_scheduled_cycle(self) -> None:
        """
        Run one scheduled cycle: wait for next execution time, then execute trading logic.
        """
        # Get configuration
        entry_days = self.validated_config.entry_days
        entry_times = self.validated_config.entry_times
        timezone_str = self.validated_config.timezone
        
        # Get timezone
        try:
            tz = pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError as e:
            self.logger.error(f"Invalid timezone '{timezone_str}': {e}")
            raise
        
        # Calculate next execution time
        next_execution = self._calculate_next_execution(entry_days, entry_times, tz)
        
        if not next_execution:
            self.logger.error("Could not calculate next execution time")
            await asyncio.sleep(3600)  # Wait an hour and try again
            return
        
        # Wait until execution time
        now = datetime.now(tz)
        wait_seconds = (next_execution - now).total_seconds()
        self.logger.info(f"Next execution scheduled for {next_execution} ({wait_seconds:.0f} seconds)")
        
        # Wait with periodic checks for stop request
        while wait_seconds > 0 and not self._stop_requested:
            sleep_time = min(10, wait_seconds)  # Check every 10s
            await asyncio.sleep(sleep_time)
            now = datetime.now(tz)
            wait_seconds = (next_execution - now).total_seconds()
        
        if self._stop_requested:
            return
        
        # Execute trading logic
        self.logger.info(f"Executing trading logic at {datetime.now(tz)}")
        await self._execute_trading_logic()
    
    def _calculate_next_execution(self, entry_days: List[str], entry_times: List[str], tz: pytz.timezone) -> Optional[datetime]:
        """
        Calculate the next execution time based on configured days and times.
        
        Args:
            entry_days: List of day names when trades can be entered
            entry_times: List of times in HH:MM format
            tz: Timezone for execution
            
        Returns:
            Next execution datetime, or None if error
        """
        now = datetime.now(tz)
        
        # Map day names to weekday numbers (0=Monday, 6=Sunday)
        day_map = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
            "Friday": 4, "Saturday": 5, "Sunday": 6
        }
        
        entry_weekdays = [day_map[day] for day in entry_days]
        
        # Parse entry times
        parsed_times = []
        for time_str in entry_times:
            try:
                hour, minute = map(int, time_str.split(":"))
                parsed_times.append(time(hour, minute))
            except (ValueError, AttributeError) as e:
                self.logger.error(f"Invalid entry_time format '{time_str}': {e}")
                continue
        
        if not parsed_times:
            return None
        
        # Find next execution time
        candidates = []
        
        # Check today and next 7 days
        for day_offset in range(8):
            check_date = (now + timedelta(days=day_offset)).date()
            check_weekday = check_date.weekday()
            
            # Skip if not a valid entry day
            if check_weekday not in entry_weekdays:
                continue
            
            # Check all entry times for this day
            for entry_time in parsed_times:
                candidate = tz.localize(datetime.combine(check_date, entry_time))
                
                # Only consider future times
                if candidate > now:
                    candidates.append(candidate)
        
        if not candidates:
            return None
        
        # Return the earliest candidate
        return min(candidates)
    
    async def _execute_trading_logic(self) -> None:
        """
        Execute the main trading logic: get option chain, select strikes, monitor prices, place order.
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            # Step 1: Get and qualify underlying contract
            underlying = await self._get_underlying_contract()
            if not underlying:
                self.logger.error("Failed to get underlying contract")
                return
            
            # Step 2: Get current underlying price
            current_price = await self._get_underlying_price(underlying)
            if not current_price:
                self.logger.error("Failed to get underlying price")
                return
            
            self.logger.info(f"Current {self.validated_config.underlying_symbol} price: {current_price:.2f}")
            
            # Step 3: Find expiration date
            expiration = await self._find_expiration(underlying)
            if not expiration:
                self.logger.error("Failed to find suitable expiration")
                return
            
            self.logger.info(f"Selected expiration: {expiration}")
            
            # Step 4: Determine strikes for all legs
            leg_strikes = await self._determine_leg_strikes(underlying, expiration, current_price)
            if not leg_strikes:
                self.logger.error("Failed to determine leg strikes")
                return
            
            # Step 5: Create option contracts for all legs
            option_contracts = await self._create_option_contracts(expiration, leg_strikes)
            if not option_contracts:
                self.logger.error("Failed to create option contracts")
                return
            
            # Step 6: Get minTick for combo contract
            min_tick = await self._get_combo_min_tick(option_contracts)
            if min_tick is None:
                self.logger.error("Failed to get combo minTick")
                return
            
            # Step 7: Monitor mid prices
            limit_price = await self._monitor_mid_prices(option_contracts)
            if limit_price is None:
                self.logger.error("Failed to determine limit price")
                return
            
            # Step 8: Apply min/max premium constraints
            limit_price = self._apply_premium_constraints(limit_price)
            
            # Step 9: Round to minTick
            limit_price = self._round_to_min_tick(limit_price, min_tick)
            
            self.logger.info(f"Final limit price: {limit_price:.2f}")
            
            # Step 10: Place strategy order
            await self._place_strategy_order(option_contracts, limit_price, min_tick)
            
        except Exception as e:
            self.logger.error(f"Error executing trading logic: {e}", exc_info=True)
    
    async def _get_underlying_contract(self) -> Optional[Contract]:
        """
        Get and qualify the underlying contract.
        
        Returns:
            Qualified underlying contract, or None if error
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            underlying_type = self.validated_config.underlying_type
            symbol = self.validated_config.underlying_symbol
            exchange = self.validated_config.underlying_exchange
            
            if underlying_type == "Index":
                contract = Index(symbol, exchange, "USD")
            else:  # Stock
                contract = Stock(symbol, exchange, "USD")
            
            qualified = await self.ib.qualifyContractsAsync(contract)
            
            if not qualified or not isinstance(qualified[0], Contract):
                self.logger.error(f"Failed to qualify {underlying_type} contract for {symbol}")
                return None
            
            self.logger.info(f"Qualified {underlying_type} contract: {symbol}")
            return qualified[0]
            
        except Exception as e:
            self.logger.error(f"Error getting underlying contract: {e}", exc_info=True)
            return None
    
    async def _get_underlying_price(self, underlying: Contract) -> Optional[float]:
        """
        Get current price of the underlying.
        
        Args:
            underlying: Qualified underlying contract
            
        Returns:
            Current price, or None if error
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            ticker = self.ib.reqMktData(underlying, "", False, False)
            await asyncio.sleep(2)  # Wait for market data
            
            if not ticker.last or ticker.last <= 0:
                self.logger.warning("Could not get current price from ticker.last")
                self.ib.cancelMktData(underlying)
                return None
            
            price = ticker.last
            self.ib.cancelMktData(underlying)
            return price
            
        except Exception as e:
            self.logger.error(f"Error getting underlying price: {e}", exc_info=True)
            return None
    
    async def _find_expiration(self, underlying: Contract) -> Optional[str]:
        """
        Find the appropriate expiration date based on DTE configuration.
        
        Args:
            underlying: Qualified underlying contract
            
        Returns:
            Expiration date in YYYYMMDD format, or None if not found
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            symbol = self.validated_config.underlying_symbol
            underlying_type = self.validated_config.underlying_type
            dte = self.validated_config.dte
            use_exact_dte = self.validated_config.use_exact_dte
            
            # Get option chains
            sec_type = "IND" if underlying_type == "Index" else "STK"
            chains = await self.ib.reqSecDefOptParamsAsync(symbol, "", sec_type, underlying.conId)
            
            if not chains:
                self.logger.error(f"No option chains found for {symbol}")
                return None
            
            # Get all available expirations
            all_expirations = set()
            for chain in chains:
                all_expirations.update(chain.expirations)
            
            if not all_expirations:
                self.logger.error("No expirations found in option chains")
                return None
            
            # Calculate target expiration date
            target_date = datetime.now().date() + timedelta(days=dte)
            target_str = target_date.strftime("%Y%m%d")
            
            self.logger.info(f"Target expiration (DTE={dte}): {target_str}")
            
            # If exact DTE required, check if it exists
            if use_exact_dte:
                if target_str in all_expirations:
                    return target_str
                else:
                    self.logger.error(f"Exact DTE {dte} not found (target: {target_str})")
                    return None
            
            # Otherwise, search for closest expiration
            # Try target first, then +1, -1, +2, -2, etc.
            max_offset = 3  # Search up to 3 days away
            
            for offset in range(max_offset + 1):
                # Try positive offset
                if offset > 0:
                    check_date = target_date + timedelta(days=offset)
                    check_str = check_date.strftime("%Y%m%d")
                    if check_str in all_expirations:
                        self.logger.info(f"Found expiration {check_str} (offset: +{offset} days from target)")
                        return check_str
                
                # Try negative offset
                if offset > 0:
                    check_date = target_date - timedelta(days=offset)
                    check_str = check_date.strftime("%Y%m%d")
                    if check_str in all_expirations:
                        self.logger.info(f"Found expiration {check_str} (offset: -{offset} days from target)")
                        return check_str
                
                # Try exact match (offset=0)
                if offset == 0 and target_str in all_expirations:
                    return target_str
            
            self.logger.error(f"No suitable expiration found within {max_offset} days of target")
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding expiration: {e}", exc_info=True)
            return None
    
    async def _determine_leg_strikes(self, underlying: Contract, expiration: str, current_price: float) -> Optional[Dict[str, float]]:
        """
        Determine strikes for all legs based on configuration.
        
        Args:
            underlying: Qualified underlying contract
            expiration: Expiration date in YYYYMMDD format
            current_price: Current underlying price
            
        Returns:
            Dictionary mapping leg name to strike price, or None if error
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            leg_strikes = {}
            symbol = self.validated_config.underlying_symbol
            underlying_type = self.validated_config.underlying_type
            
            # Get option chains for delta-based selection
            chains = None
            if any(leg.strike_selection == "delta" for leg in self.validated_config.legs):
                sec_type = "IND" if underlying_type == "Index" else "STK"
                chains = await self.ib.reqSecDefOptParamsAsync(symbol, "", sec_type, underlying.conId)
                if not chains:
                    self.logger.error(f"No option chains found for {symbol}")
                    return None
            
            # First pass: determine strikes for legs with underlying_offset and delta
            for leg_config in self.validated_config.legs:
                if leg_config.strike_selection == "underlying_offset":
                    strike = current_price + leg_config.strike_offset
                    # Round to nearest valid strike (typically 5 or 1 point increments)
                    strike = self._round_to_valid_strike(strike)
                    leg_strikes[leg_config.name] = strike
                    self.logger.info(f"Leg '{leg_config.name}': strike={strike:.2f} (underlying_offset={leg_config.strike_offset})")
                
                elif leg_config.strike_selection == "delta":
                    # Find strike by delta
                    target_delta = leg_config.strike_selection_delta
                    right = "C" if leg_config.right == "call" else "P"
                    
                    # Get available strikes from chains
                    available_strikes = set()
                    for chain in chains:
                        if expiration in chain.expirations:
                            available_strikes.update(chain.strikes)
                    
                    if not available_strikes:
                        self.logger.error(f"No strikes available for expiration {expiration}")
                        return None
                    
                    # Sort strikes appropriately based on option type
                    if right == "P":
                        # For puts, search strikes below current price (descending order)
                        strikes = sorted([s for s in available_strikes if s < current_price], reverse=True)
                    else:
                        # For calls, search strikes above current price (ascending order)
                        strikes = sorted([s for s in available_strikes if s > current_price])
                    
                    if not strikes:
                        self.logger.error(f"No suitable strikes found for {right} options")
                        return None
                    
                    # Use shared utility function to find strike by delta
                    strike = await find_option_by_delta(
                        self.ib, self.logger, strikes, symbol, expiration, right, target_delta
                    )
                    
                    if not strike:
                        self.logger.error(f"Could not find strike with delta ~{target_delta} for leg '{leg_config.name}'")
                        return None
                    
                    leg_strikes[leg_config.name] = strike
                    self.logger.info(f"Leg '{leg_config.name}': strike={strike:.2f} (delta={target_delta})")
            
            # Second pass: determine strikes for legs with leg_offset
            for leg_config in self.validated_config.legs:
                if leg_config.strike_selection == "leg_offset":
                    parent_name = leg_config.strike_selection_parent
                    if parent_name not in leg_strikes:
                        self.logger.error(f"Parent leg '{parent_name}' not found for leg '{leg_config.name}'")
                        return None
                    
                    parent_strike = leg_strikes[parent_name]
                    strike = parent_strike + leg_config.strike_offset
                    strike = self._round_to_valid_strike(strike)
                    leg_strikes[leg_config.name] = strike
                    self.logger.info(f"Leg '{leg_config.name}': strike={strike:.2f} (leg_offset={leg_config.strike_offset} from '{parent_name}')")
            
            return leg_strikes
            
        except Exception as e:
            self.logger.error(f"Error determining leg strikes: {e}", exc_info=True)
            return None
    
    def _round_to_valid_strike(self, strike: float) -> float:
        """
        Round strike to nearest valid increment.
        
        For most options, strikes are in 5-point or 1-point increments.
        This uses 5-point increments for strikes >= 100, 1-point for < 100.
        
        Args:
            strike: Raw strike price
            
        Returns:
            Rounded strike price
        """
        if strike >= 100:
            # Round to nearest 5
            return round(strike / 5) * 5
        else:
            # Round to nearest 1
            return round(strike)
    
    async def _create_option_contracts(self, expiration: str, leg_strikes: Dict[str, float]) -> Optional[List[Tuple[StrategyLegConfig, Contract]]]:
        """
        Create and qualify option contracts for all legs.
        
        Args:
            expiration: Expiration date in YYYYMMDD format
            leg_strikes: Dictionary mapping leg name to strike price
            
        Returns:
            List of tuples (leg_config, qualified_contract), or None if error
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            symbol = self.validated_config.underlying_symbol
            option_contracts = []
            
            for leg_config in self.validated_config.legs:
                strike = leg_strikes[leg_config.name]
                right = "C" if leg_config.right == "call" else "P"
                
                # Create option contract
                option = Option(symbol, expiration, strike, right, "SMART", currency="USD")
                
                # Qualify contract
                qualified = await self.ib.qualifyContractsAsync(option)
                
                if not qualified or not isinstance(qualified[0], Contract):
                    self.logger.error(f"Failed to qualify option contract for leg '{leg_config.name}'")
                    return None
                
                option_contracts.append((leg_config, qualified[0]))
                self.logger.info(f"Qualified option for leg '{leg_config.name}': {symbol} {expiration} {strike} {right}")
            
            return option_contracts
            
        except Exception as e:
            self.logger.error(f"Error creating option contracts: {e}", exc_info=True)
            return None
    
    async def _monitor_mid_prices(self, option_contracts: List[Tuple[StrategyLegConfig, Contract]]) -> Optional[float]:
        """
        Monitor mid prices for all legs and calculate mean mid price for the combo.
        
        Args:
            option_contracts: List of tuples (leg_config, contract)
            
        Returns:
            Mean mid price for the combo, or None if error
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            monitoring_period = self.validated_config.mid_price_monitoring_period
            self.logger.info(f"Monitoring mid prices for {monitoring_period} seconds")
            
            # Subscribe to market data for all legs
            tickers = []
            for leg_config, contract in option_contracts:
                ticker = self.ib.reqMktData(contract, "", False, False)
                tickers.append((leg_config, ticker))
            
            # Wait for initial data
            await asyncio.sleep(2)
            
            # Collect mid prices over the monitoring period
            combo_mid_prices = []
            start_time = asyncio.get_event_loop().time()
            
            while (asyncio.get_event_loop().time() - start_time) < monitoring_period:
                # Calculate combo mid price
                combo_mid = 0.0
                valid = True
                
                for leg_config, ticker in tickers:
                    if ticker.bid and ticker.ask and ticker.bid > 0 and ticker.ask > 0:
                        leg_mid = (ticker.bid + ticker.ask) / 2
                        # Apply ratio (negative for sell, positive for buy)
                        combo_mid += leg_mid * leg_config.ratio
                    else:
                        valid = False
                        break
                
                # Only include valid prices that are non-negative
                if valid and combo_mid >= 0:
                    combo_mid_prices.append(combo_mid)
                    self.logger.debug(f"Combo mid price: {combo_mid:.2f}")
                elif valid:
                    self.logger.debug(f"Skipping negative combo mid price: {combo_mid:.2f}")
                
                await asyncio.sleep(1)  # Sample every second
            
            # Cancel market data subscriptions
            for _, ticker in tickers:
                self.ib.cancelMktData(ticker.contract)
            
            if not combo_mid_prices:
                self.logger.error("No valid mid prices collected")
                return None
            
            # Calculate mean mid price
            mean_mid = sum(combo_mid_prices) / len(combo_mid_prices)
            self.logger.info(f"Mean mid price: {mean_mid:.2f} (from {len(combo_mid_prices)} samples)")
            
            return mean_mid
            
        except Exception as e:
            self.logger.error(f"Error monitoring mid prices: {e}", exc_info=True)
            return None
    
    async def _get_combo_min_tick(self, option_contracts: List[Tuple[StrategyLegConfig, Contract]]) -> Optional[float]:
        """
        Get the minTick requirement for the combo contract.
        
        Args:
            option_contracts: List of tuples (leg_config, contract)
            
        Returns:
            minTick value, or None if error
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            # Create combo contract
            combo = Contract()
            combo.symbol = self.validated_config.underlying_symbol
            combo.secType = "BAG"
            combo.currency = "USD"
            combo.exchange = "SMART"
            
            combo.comboLegs = []
            for leg_config, contract in option_contracts:
                leg = ComboLeg()
                leg.conId = contract.conId
                leg.ratio = abs(leg_config.ratio)
                leg.action = "SELL" if leg_config.ratio < 0 else "BUY"
                leg.exchange = "SMART"
                combo.comboLegs.append(leg)
            
            # Request contract details
            details_list = await self.ib.reqContractDetailsAsync(combo)
            
            if not details_list:
                self.logger.error("Failed to get contract details for combo")
                return None
            
            # Get minTick from first contract details
            min_tick = details_list[0].minTick
            self.logger.info(f"Combo minTick: {min_tick}")
            
            return min_tick
            
        except Exception as e:
            self.logger.error(f"Error getting combo minTick: {e}", exc_info=True)
            return None
    
    def _round_to_min_tick(self, price: float, min_tick: float) -> float:
        """
        Round price to nearest valid minTick increment.
        
        Args:
            price: Raw price
            min_tick: Minimum tick size
            
        Returns:
            Rounded price
        """
        if min_tick <= 0:
            return price
        
        # Round to nearest minTick
        rounded = round(price / min_tick) * min_tick
        
        # Round to avoid floating point precision issues
        # Determine decimal places based on minTick
        if min_tick >= 1:
            decimal_places = 0
        elif min_tick >= 0.1:
            decimal_places = 1
        elif min_tick >= 0.01:
            decimal_places = 2
        else:
            decimal_places = 3
        
        rounded = round(rounded, decimal_places)
        
        self.logger.debug(f"Rounded price {price:.4f} to {rounded:.4f} (minTick={min_tick})")
        
        return rounded
    
    def _apply_premium_constraints(self, limit_price: float) -> float:
        """
        Apply min/max premium constraints to the limit price.
        
        Args:
            limit_price: Calculated limit price
            
        Returns:
            Adjusted limit price
        """
        min_premium = self.validated_config.min_premium
        max_premium = self.validated_config.max_premium
        
        original_price = limit_price
        
        if min_premium is not None and limit_price < min_premium:
            limit_price = min_premium
            self.logger.info(f"Adjusted limit price from {original_price:.2f} to min_premium {limit_price:.2f}")
        
        if max_premium is not None and limit_price > max_premium:
            limit_price = max_premium
            self.logger.info(f"Adjusted limit price from {original_price:.2f} to max_premium {limit_price:.2f}")
        
        return limit_price
    
    async def _place_strategy_order(self, option_contracts: List[Tuple[StrategyLegConfig, Contract]], limit_price: float, min_tick: float) -> None:
        """
        Place the strategy order and monitor for fill, adjusting price if needed.
        
        Args:
            option_contracts: List of tuples (leg_config, contract)
            limit_price: Initial limit price
            min_tick: Minimum tick size for the combo
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            num_contracts = self.validated_config.num_contracts
            max_adjustments = self.validated_config.max_price_adjustments
            
            # Create combo contract
            combo = Contract()
            combo.symbol = self.validated_config.underlying_symbol
            combo.secType = "BAG"
            combo.currency = "USD"
            combo.exchange = "SMART"
            
            combo.comboLegs = []
            for leg_config, contract in option_contracts:
                leg = ComboLeg()
                leg.conId = contract.conId
                leg.ratio = abs(leg_config.ratio)
                leg.action = "SELL" if leg_config.ratio < 0 else "BUY"
                leg.exchange = "SMART"
                combo.comboLegs.append(leg)
            
            # Create limit order
            order = LimitOrder("BUY", num_contracts, limit_price)
            order.orderRef = f"{self.bot_id}_strategy"
            
            self.logger.info(f"Placing strategy order: {num_contracts} @ {limit_price:.2f}")
            
            # Place order
            trade = self.ib.placeOrder(combo, order)
            self._active_trades.append(trade)
            
            # Monitor for fill with price adjustments
            adjustments_made = 0
            current_limit = limit_price
            initial_limit = limit_price  # Store initial limit for calculating adjustments
            
            while adjustments_made <= max_adjustments:
                # Wait 30 seconds for fill
                await asyncio.sleep(30)
                
                # Check order status
                if trade.orderStatus.status in ["Filled", "Cancelled"]:
                    break
                
                # If not filled and we can still adjust, adjust price by minTick
                if adjustments_made < max_adjustments:
                    adjustments_made += 1
                    
                    # Calculate new price based on initial limit, not current limit
                    # Determine adjustment direction based on whether this is a credit or debit
                    if initial_limit < 0:
                        # Credit spread - reduce credit (move toward zero)
                        current_limit = initial_limit - (min_tick * adjustments_made)
                    else:
                        # Debit spread - increase debit (willing to pay more to get filled)
                        current_limit = initial_limit + (min_tick * adjustments_made)
                    
                    # Apply premium constraints
                    current_limit = self._apply_premium_constraints(current_limit)
                    
                    # Round to minTick to ensure valid price
                    current_limit = self._round_to_min_tick(current_limit, min_tick)
                    
                    self.logger.info(f"Order not filled - adjusting price to {current_limit:.2f} (adjustment {adjustments_made}/{max_adjustments})")
                    
                    # Modify order
                    order.lmtPrice = current_limit
                    self.ib.placeOrder(combo, order)
                else:
                    # Max adjustments reached, cancel order
                    self.logger.warning("Max price adjustments reached - cancelling order")
                    self.ib.cancelOrder(order)
                    break
            
            # Log final status
            final_status = trade.orderStatus.status
            if final_status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                self.logger.info(f"Order filled at {fill_price:.2f}")
                
                # Place bracket orders if configured
                await self._place_bracket_orders(combo, num_contracts, fill_price)
            else:
                self.logger.warning(f"Order not filled - final status: {final_status}")
            
        except Exception as e:
            self.logger.error(f"Error placing strategy order: {e}", exc_info=True)
    
    async def _place_bracket_orders(self, combo: Contract, quantity: int, fill_price: float) -> None:
        """
        Place bracket orders (stop loss and take profit) if configured.
        
        Args:
            combo: Combo contract
            quantity: Number of contracts
            fill_price: Fill price of the opening order
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            stoploss_factor = self.validated_config.stoploss_factor
            takeprofit_factor = self.validated_config.takeprofit_factor
            
            if stoploss_factor is None and takeprofit_factor is None:
                self.logger.info("No bracket orders configured")
                return
            
            # Determine if this was a credit or debit
            is_credit = fill_price < 0
            
            # Place stop loss order
            if stoploss_factor is not None:
                if is_credit:
                    # Credit: stop loss is at a larger debit (more negative)
                    # Example: fill at -2.0, factor 1.5 -> stop at -3.0
                    stop_price = fill_price * (1 + stoploss_factor)
                else:
                    # Debit: stop loss is at a smaller value (closer to zero)
                    # Example: fill at 2.0, factor 0.2 -> stop at 0.4
                    stop_price = fill_price * stoploss_factor
                
                stop_order = StopOrder("SELL", quantity, stop_price)
                stop_order.orderRef = f"{self.bot_id}_strategy_stoploss"
                stop_order.tif = "GTC"
                
                self.logger.info(f"Placing stop loss order: SELL {quantity} @ STP {stop_price:.2f}")
                stop_trade = self.ib.placeOrder(combo, stop_order)
                self._active_trades.append(stop_trade)
            
            # Place take profit order
            if takeprofit_factor is not None:
                if is_credit:
                    # Credit: take profit is at a smaller credit (closer to zero)
                    # Example: fill at -2.0, factor 0.2 -> profit at -0.4
                    profit_price = fill_price * takeprofit_factor
                else:
                    # Debit: take profit is at a larger value
                    # Example: fill at 2.0, factor 1.6 -> profit at 3.2
                    profit_price = fill_price * (1 + takeprofit_factor)
                
                profit_order = LimitOrder("SELL", quantity, profit_price)
                profit_order.orderRef = f"{self.bot_id}_strategy_takeprofit"
                profit_order.tif = "GTC"
                
                self.logger.info(f"Placing take profit order: SELL {quantity} @ LMT {profit_price:.2f}")
                profit_trade = self.ib.placeOrder(combo, profit_order)
                self._active_trades.append(profit_trade)
            
        except Exception as e:
            self.logger.error(f"Error placing bracket orders: {e}", exc_info=True)
    
    async def stop(self) -> None:
        """
        Stop the strategy bot.
        """
        self.logger.info("Stopping Strategy bot")
        self._stop_requested = True
        
        # Cancel any active orders
        if self._active_trades and self.ib:
            for trade in self._active_trades:
                try:
                    if trade.orderStatus.status not in ["Filled", "Cancelled"]:
                        self.ib.cancelOrder(trade.order)
                        self.logger.info(f"Cancelled order: {trade.order.orderRef}")
                except Exception as e:
                    self.logger.error(f"Error cancelling order: {e}", exc_info=True)
        
        # Don't disconnect - the shared connection is managed by the framework
        self.ib = None
        
        self.logger.info("Strategy bot stopped")
