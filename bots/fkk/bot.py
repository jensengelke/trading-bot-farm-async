import sys
from pathlib import Path
from typing import Any, Dict, Optional, List
from datetime import datetime, time, timedelta
import asyncio

# Add parent directory to path to import framework modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ib_async import IB, Index, Option, Contract, Order, Trade
from ib_async.order import LimitOrder
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
import pytz

# Import bot config schema
try:
    from bots.fkk.config import FKKBotConfig
except ImportError:
    # Fallback if running from different context
    from .config import FKKBotConfig


@trace_all_methods
class Bot(BotBase):
    """
    FKK Bot Implementation.
    
    Runs once per day at a configurable time, monitors market conditions,
    and opens a 0 DTE bull put spread on SPX if conditions are met.
    """
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager):
        """
        Initialize the FKK bot.
        
        Args:
            bot_id: Unique identifier for this bot instance
            config: Bot-specific configuration (raw dict)
            system_config: System-wide configuration
            ib_connection_manager: Shared IB connection manager
        """
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        # Validate configuration using Pydantic model
        try:
            self.validated_config = FKKBotConfig(**config)
            self.logger.info("Configuration validated successfully")
        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}", exc_info=True)
            raise ValueError(f"Invalid configuration for bot {bot_id}: {e}") from e
        
        self.ib: Optional[IB] = None
        self._stop_requested = False
        self._active_order: Optional[Trade] = None
        
    async def start(self) -> None:
        """
        Start the bull put spread bot.
        
        Runs continuously, executing the trading logic once per day at the configured time.
        """
        self.logger.info("Starting Bull Put Spread bot")
        
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
                await self._run_daily_cycle()
            except Exception as e:
                self.logger.error(f"Error in daily cycle: {e}", exc_info=True)
                # Wait before retrying
                await asyncio.sleep(60)
    
    async def _run_daily_cycle(self) -> None:
        """
        Run one daily cycle: wait for execution time, then execute trading logic.
        """
        # Get configuration
        execution_time_str = self.config.get("execution_time", "14:13")
        timezone_str = self.config.get("timezone", "America/New_York")
        
        # Parse execution time
        try:
            hour, minute = map(int, execution_time_str.split(":"))
            execution_time = time(hour, minute)
        except (ValueError, AttributeError) as e:
            self.logger.error(f"Invalid execution_time format '{execution_time_str}': {e}")
            raise
        
        # Get timezone
        try:
            tz = pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError as e:
            self.logger.error(f"Invalid timezone '{timezone_str}': {e}")
            raise
        
        # Calculate next execution time
        now = datetime.now(tz)
        next_execution = tz.localize(datetime.combine(now.date(), execution_time))
        
        # If execution time has passed today, schedule for tomorrow
        if now >= next_execution:
            next_execution += timedelta(days=1)
        
        # Wait until execution time
        wait_seconds = (next_execution - now).total_seconds()
        self.logger.info(f"Next execution scheduled for {next_execution} ({wait_seconds:.0f} seconds)")
        
        # Wait with periodic checks for stop request
        while wait_seconds > 0 and not self._stop_requested:
            sleep_time = min(10, wait_seconds)  # Check every 10s
            await asyncio.sleep(sleep_time)
            wait_seconds -= sleep_time
        
        if self._stop_requested:
            return
        
        # Execute trading logic
        self.logger.info(f"Executing trading logic at {datetime.now(tz)}")
        await self._execute_trading_logic()
    
    async def _execute_trading_logic(self) -> None:
        """
        Execute the main trading logic: check conditions and place trade if met.
        """
        timeout_minutes = self.config.get("condition_timeout_minutes", 5)
        timeout_seconds = timeout_minutes * 60
        start_time = asyncio.get_event_loop().time()
        
        self.logger.info(f"Starting condition monitoring for {timeout_minutes} minutes")
        
        # Monitor conditions with timeout
        while (asyncio.get_event_loop().time() - start_time) < timeout_seconds:
            if self._stop_requested:
                return
            
            # Check all conditions
            conditions_met = await self._check_all_conditions()
            
            if conditions_met:
                self.logger.info("All conditions met - placing trade")
                await self._place_bull_put_spread()
                return
            
            # Wait before checking again (check every 10 seconds)
            await asyncio.sleep(10)
        
        self.logger.info("Condition monitoring timeout reached - no trade placed")
    
    async def _check_all_conditions(self) -> bool:
        """
        Check all entry conditions.
        
        Returns:
            True if all conditions are met, False otherwise
        """

        assert self.ib is not None, "IB connection is not initialized"

        try:
            # Create SPX index contract
            spx = Index("SPX", "CBOE", "USD")
            spx = (await self.ib.qualifyContractsAsync(spx))[0]

            # assert that spx is of type Contract
            if not isinstance(spx, Contract):
                self.logger.error("Failed to qualify SPX contract. Return value is not a Contract instance.")
                return False
            
            # Condition 1: Check if SPX is tradeable and today is Tuesday-Friday
            if not await self._check_tradeable_and_weekday(spx):
                return False
            
            # Get current SPX price
            ticker = self.ib.reqMktData(spx, "", False, False)
            await asyncio.sleep(2)  # Wait for market data
            
            if not ticker.last or ticker.last <= 0:
                self.logger.warning("Could not get current SPX price")
                self.ib.cancelMktData(spx)
                return False
            
            current_price = ticker.last
            self.logger.debug(f"Current SPX price: {current_price}")
            
            # Condition 2: Check if price is above 5-day SMA
            if not await self._check_above_5day_sma(spx, current_price):
                self.ib.cancelMktData(spx)
                return False
            
            # Condition 3: Check if price is 0.3% or more above today's open
            if not await self._check_above_open(spx, current_price):
                self.ib.cancelMktData(spx)
                return False
            
            self.ib.cancelMktData(spx)
            self.logger.info("All conditions met!")
            return True
            
        except Exception as e:
            self.logger.error(f"Error checking conditions: {e}", exc_info=True)
            return False
    
    async def _check_tradeable_and_weekday(self, spx: Contract) -> bool:
        """
        Check if SPX is currently tradeable and today is Tuesday-Friday.
        
        Args:
            spx: Qualified SPX contract
            
        Returns:
            True if tradeable and correct weekday, False otherwise
        """

        assert self.ib is not None, "IB connection is not initialized"

        # Check weekday (0=Monday, 6=Sunday)
        today_weekday = datetime.now().weekday()
        if today_weekday == 0:  # Monday
            self.logger.info("Today is Monday - skipping trade")
            return False
        
        self.logger.debug(f"Weekday check passed (weekday={today_weekday})")
        
        # Get contract details to check trading hours
        details = await self.ib.reqContractDetailsAsync(spx)
        if not details:
            self.logger.warning("Could not get SPX contract details")
            return False
        
        trading_hours = details[0].tradingHours
        timezone_id = details[0].timeZoneId
        self.logger.debug(f"SPX trading hours: {trading_hours}")
        self.logger.debug(f"SPX timezone: {timezone_id}")
        
        # Parse trading hours to check if currently tradeable
        # Format: "20260626:0830-20260626:1500;20260627:CLOSED;20260628:CLOSED;..."
        # The first segment before the semicolon is today's trading hours
        
        # Split by semicolon and get the first segment (today)
        segments = trading_hours.split(';')
        if not segments:
            self.logger.warning("Could not parse trading hours")
            return False
        
        today_segment = segments[0]
        self.logger.debug(f"Today's trading hours segment: {today_segment}")
        
        # Check if today is closed
        if "CLOSED" in today_segment.upper():
            self.logger.info("SPX market is closed today")
            return False
        
        # Parse today's segment: "20260626:0830-20260626:1500"
        # Format: YYYYMMDD:HHMM-YYYYMMDD:HHMM
        try:
            # Split by dash to get open and close times
            parts = today_segment.split('-')
            if len(parts) != 2:
                self.logger.warning(f"Unexpected trading hours format: {today_segment}")
                return False
            
            open_part = parts[0]  # "20260626:0830"
            close_part = parts[1]  # "20260626:1500"
            
            # Extract time portions
            open_time_str = open_part.split(':')[1]  # "0830"
            close_time_str = close_part.split(':')[1]  # "1500"
            
            # Get current time in exchange timezone
            if timezone_id:
                try:
                    exchange_tz = pytz.timezone(timezone_id)
                    now_exchange = datetime.now(exchange_tz)
                except pytz.exceptions.UnknownTimeZoneError:
                    self.logger.warning(f"Unknown timezone: {timezone_id}, using local time")
                    now_exchange = datetime.now()
            else:
                self.logger.warning("No timezone info, using local time")
                now_exchange = datetime.now()
            
            # Parse open and close times
            open_hour = int(open_time_str[:2])
            open_minute = int(open_time_str[2:])
            close_hour = int(close_time_str[:2])
            close_minute = int(close_time_str[2:])
            
            # Create time objects for comparison
            market_open = now_exchange.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
            market_close = now_exchange.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
            
            self.logger.info(f"Market hours: {market_open.strftime('%H:%M')} - {market_close.strftime('%H:%M')} {timezone_id}")
            self.logger.info(f"Current time: {now_exchange.strftime('%H:%M:%S')} {timezone_id}")
            
            # Check if current time is within trading hours
            if market_open <= now_exchange <= market_close:
                self.logger.info("SPX market is currently open")
                return True
            else:
                self.logger.info("SPX market is currently closed (outside trading hours)")
                return False
                
        except (ValueError, IndexError) as e:
            self.logger.error(f"Error parsing trading hours: {e}", exc_info=True)
            return False
    
    async def _check_above_5day_sma(self, spx: Contract, current_price: float) -> bool:
        """
        Check if current price is above 5-day simple moving average.
        
        The 5-day SMA is calculated as: (sum of last 4 daily closes + current price) / 5
        
        Args:
            spx: Qualified SPX contract
            current_price: Current SPX price
            
        Returns:
            True if above SMA, False otherwise
        """

        assert self.ib is not None, "IB connection is not initialized"

        try:
            # Get last 5 days of daily bars
            bars = await self.ib.reqHistoricalDataAsync(
                spx,
                endDateTime="",
                durationStr="5 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True
            )
            
            if len(bars) < 4:
                self.logger.warning(f"Not enough historical data (got {len(bars)} bars, need 4)")
                return False
            
            # Calculate 5-day SMA: sum of last 4 closes + current price, divided by 5
            last_4_closes = [bar.close for bar in bars[-4:]]
            sma_5 = (sum(last_4_closes) + current_price) / 5
            
            self.logger.info(f"5-day SMA: {sma_5:.2f}, Current price: {current_price:.2f}")
            
            if current_price > sma_5:
                self.logger.info(f"Price is above 5-day SMA ({current_price:.2f} > {sma_5:.2f})")
                return True
            else:
                self.logger.info(f"Price is below 5-day SMA ({current_price:.2f} <= {sma_5:.2f})")
                return False
                
        except Exception as e:
            self.logger.error(f"Error calculating 5-day SMA: {e}", exc_info=True)
            return False
    
    async def _check_above_open(self, spx: Contract, current_price: float) -> bool:
        """
        Check if current price is above today's open by the configured threshold.
        
        Args:
            spx: Qualified SPX contract
            current_price: Current SPX price
            
        Returns:
            True if above threshold, False otherwise
        """
        assert self.ib is not None, "IB connection is not initialized"
        try:
            # Get today's bar
            bars = await self.ib.reqHistoricalDataAsync(
                spx,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True
            )
            
            if not bars:
                self.logger.warning("Could not get today's open price")
                return False
            
            today_open = bars[-1].open
            
            # Get threshold from configuration (default 0.003 = 0.3%)
            threshold_pct = self.validated_config.price_above_open_threshold
            threshold = today_open * (1 + threshold_pct)
            
            self.logger.info(f"Today's open: {today_open:.2f}, Threshold ({threshold_pct*100:.1f}% above): {threshold:.2f}, Current: {current_price:.2f}")
            
            if current_price >= threshold:
                self.logger.info(f"Price is {threshold_pct*100:.1f}%+ above open ({current_price:.2f} >= {threshold:.2f})")
                return True
            else:
                self.logger.info(f"Price is below {threshold_pct*100:.1f}% threshold ({current_price:.2f} < {threshold:.2f})")
                return False
                
        except Exception as e:
            self.logger.error(f"Error checking price vs open: {e}", exc_info=True)
            return False
    
    async def _place_bull_put_spread(self) -> None:
        """
        Place a bull put spread order on SPX with 0 DTE.
        """
        assert self.ib is not None, "IB connection is not initialized"
        try:
            num_contracts = self.config.get("num_contracts", 1)
            spread_width = self.config.get("spread_width", 10)
            max_price_adjustments = self.config.get("max_price_adjustments", 4)
            
            self.logger.info(f"Placing bull put spread: {num_contracts} contracts, width={spread_width}")
            
            # Get 0 DTE options
            short_put, long_put = await self._find_spread_legs(spread_width)
            
            if not short_put or not long_put:
                self.logger.error("Could not find suitable option contracts")
                return
            
            # Get market data for pricing
            short_ticker = self.ib.reqMktData(short_put, "", False, False)
            long_ticker = self.ib.reqMktData(long_put, "", False, False)
            await asyncio.sleep(2)  # Wait for market data
            
            # Calculate mid price
            short_mid = (short_ticker.bid + short_ticker.ask) / 2 if short_ticker.bid and short_ticker.ask else None
            long_mid = (long_ticker.bid + long_ticker.ask) / 2 if long_ticker.bid and long_ticker.ask else None
            
            if not short_mid or not long_mid:
                self.logger.error("Could not get option prices")
                self.ib.cancelMktData(short_put)
                self.ib.cancelMktData(long_put)
                return
            
            # Net credit for the spread (we sell short put, buy long put)
            net_credit = short_mid - long_mid
            
            # Round to min tick (0.05 for SPX options)
            min_tick = 0.05
            limit_price = round(net_credit / min_tick) * min_tick
            
            self.logger.info(f"Short put mid: {short_mid:.2f}, Long put mid: {long_mid:.2f}, Net credit: {net_credit:.2f}, Limit price: {limit_price:.2f}")
            
            self.ib.cancelMktData(short_put)
            self.ib.cancelMktData(long_put)
            
            # Create combo order (bull put spread)
            await self._place_spread_order(short_put, long_put, num_contracts, limit_price, max_price_adjustments)
            
        except Exception as e:
            self.logger.error(f"Error placing bull put spread: {e}", exc_info=True)
    
    async def _find_spread_legs(self, spread_width: int) -> tuple[Optional[Contract], Optional[Contract]]:
        """
        Find the short and long put options for the spread.
        
        Args:
            spread_width: Width of the spread in strike points
            
        Returns:
            Tuple of (short_put, long_put) contracts, or (None, None) if not found
        """
        assert self.ib is not None, "IB connection is not initialized"
        try:
            # Get SPX index
            spx = Index("SPX", "CBOE", "USD")
            spx = (await self.ib.qualifyContractsAsync(spx))[0]

            if not spx or not isinstance(spx, Contract):
                self.logger.error("Failed to qualify SPX contract: Return value is not a Contract instance.")
                return None, None
            
            # Get current SPX price
            ticker = self.ib.reqMktData(spx, "", False, False)
            await asyncio.sleep(2)
            current_price = ticker.last
            self.ib.cancelMktData(spx)
            
            self.logger.info(f"Finding 0 DTE options for SPX at {current_price:.2f}")
            
            # Get option chains
            chains = await self.ib.reqSecDefOptParamsAsync("SPX", "", "IND", 0)
            
            if not chains:
                self.logger.error("No option chains found for SPX")
                return None, None
            
            # Find 0 DTE expiration (today's date) for SPXW trading class
            today = datetime.now().strftime("%Y%m%d")
            
            chain = None
            for c in chains:
                if today in c.expirations and c.tradingClass == "SPXW":
                    chain = c
                    break
            
            if not chain:
                self.logger.error(f"No 0 DTE SPXW options found for {today}")
                return None, None
            
            self.logger.info(f"Found 0 DTE SPXW chain with expiration {today}")
            
            # Get strikes below current price (for puts)
            strikes = sorted([s for s in chain.strikes if s < current_price], reverse=True)
            
            if len(strikes) < 2:
                self.logger.error("Not enough strikes available")
                return None, None
            
            # Find short put with delta closest to 0.35
            short_strike = await self._find_strike_by_delta(strikes, today, current_price, target_delta=0.35)
            
            if not short_strike:
                self.logger.error("Could not find short strike with delta ~0.35")
                return None, None
            
            # Long put is spread_width below short put
            long_strike = short_strike - spread_width
            
            if long_strike not in chain.strikes:
                self.logger.error(f"Long strike {long_strike} not available")
                return None, None
            
            self.logger.info(f"Selected strikes: Short={short_strike}, Long={long_strike}")
            
            # Create option contracts
            short_put = Option("SPX", today, short_strike, "P", "SMART", currency="USD")
            long_put = Option("SPX", today, long_strike, "P", "SMART", currency="USD")
            
            # Qualify contracts
            short_put = (await self.ib.qualifyContractsAsync(short_put))[0]
            long_put = (await self.ib.qualifyContractsAsync(long_put))[0]
            
            if not short_put or not isinstance(short_put, Contract):
                self.logger.error("Failed to qualify short put contract")
                return None, None
            
            if not long_put or not isinstance(long_put, Contract):
                self.logger.error("Failed to qualify long put contract")
                return None, None
            
            return short_put, long_put
            
        except Exception as e:
            self.logger.error(f"Error finding spread legs: {e}", exc_info=True)
            return None, None
    
    async def _find_strike_by_delta(self, strikes: List[float], expiration: str, current_price: float, target_delta: float) -> Optional[float]:
        """
        Find the strike with delta closest to target using actual option greeks from IB.
        
        Processes strikes in batches of 20, stopping early if a delta smaller than target is found.
        This ensures we find the optimal strike even if it's beyond the first 20 strikes.
        
        Args:
            strikes: List of available strikes (sorted descending, below current price)
            expiration: Option expiration date (format: YYYYMMDD)
            current_price: Current underlying price
            target_delta: Target delta value (e.g., 0.35 for puts, which have negative delta)
            
        Returns:
            Strike with delta closest to target, or None if not found
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        try:
            self.logger.info(f"Finding strike with delta closest to {target_delta} (target absolute value)")
            
            best_strike = None
            best_delta = None
            best_diff = float('inf')
            
            batch_size = 20
            offset = 0
            
            # Process strikes in batches until we find a delta smaller than target or run out of strikes
            while offset < len(strikes):
                batch_strikes = strikes[offset:offset + batch_size]
                
                if not batch_strikes:
                    break
                
                self.logger.debug(f"Processing batch: strikes {offset} to {offset + len(batch_strikes) - 1}")
                
                # Create option contracts for this batch
                option_contracts = []
                for strike in batch_strikes:
                    option = Option("SPX", expiration, strike, "P", "SMART", currency="USD")
                    option_contracts.append((strike, option))
                
                # Qualify all contracts in batch
                contracts_to_qualify = [opt for _, opt in option_contracts]
                
                try:
                    qualified_contracts = await self.ib.qualifyContractsAsync(*contracts_to_qualify)
                except Exception as e:
                    self.logger.error(f"Error qualifying option contracts in batch: {e}", exc_info=True)
                    offset += batch_size
                    continue
                
                # Request market data with greeks for each option
                tickers = []
                for i, qualified_contract in enumerate(qualified_contracts):
                    if qualified_contract and isinstance(qualified_contract, Contract):
                        # Request market data with greeks (genericTickList "106" requests option greeks)
                        ticker = self.ib.reqMktData(qualified_contract, "106", False, False)
                        tickers.append((batch_strikes[i], ticker))
                    else:
                        self.logger.warning(f"Failed to qualify contract for strike {batch_strikes[i]}")
                
                # Wait for market data to populate
                await asyncio.sleep(3)
                
                # Track if we should stop (found delta smaller than target)
                should_stop = False
                
                # Find the strike with delta closest to target in this batch
                for strike, ticker in tickers:
                    # Check if we have model greeks and delta is a valid number
                    if (ticker.modelGreeks and 
                        ticker.modelGreeks.delta is not None and 
                        isinstance(ticker.modelGreeks.delta, (int, float))):
                        
                        delta = ticker.modelGreeks.delta
                        
                        # For puts, delta is negative. We want absolute value for comparison
                        abs_delta = abs(delta)
                        diff = abs(abs_delta - target_delta)
                        
                        self.logger.debug(f"Strike {strike}: delta={delta:.4f}, abs_delta={abs_delta:.4f}, diff={diff:.4f}")
                        
                        # Update best if this is closer
                        if diff < best_diff:
                            best_diff = diff
                            best_strike = strike
                            best_delta = delta
                        
                        # Stop if we found a delta smaller than target (we've gone too far OTM)
                        if abs_delta < target_delta:
                            self.logger.info(f"Found delta {abs_delta:.4f} < target {target_delta:.4f} at strike {strike}, stopping search")
                            should_stop = True
                            break
                    else:
                        self.logger.debug(f"Strike {strike}: No greeks available")
                
                # Cancel all market data subscriptions for this batch
                for _, ticker in tickers:
                    self.ib.cancelMktData(ticker.contract)
                
                # Stop if we found a delta smaller than target
                if should_stop:
                    break
                
                # Move to next batch
                offset += batch_size
            
            if best_strike is not None:
                self.logger.info(f"Selected strike {best_strike} with delta={best_delta:.4f} (abs={abs(best_delta):.4f}, target={target_delta:.4f}, diff={best_diff:.4f})")
                return best_strike
            else:
                self.logger.error("No suitable strike found with valid greeks")
                return None
            
        except Exception as e:
            self.logger.error(f"Error finding strike by delta: {e}", exc_info=True)
            return None
    
    async def _place_spread_order(self, short_put: Contract, long_put: Contract, quantity: int, limit_price: float, max_adjustments: int) -> None:
        """
        Place the spread order and monitor for fill, adjusting price if needed.
        
        Args:
            short_put: Short put contract
            long_put: Long put contract
            quantity: Number of spreads to trade
            limit_price: Initial limit price
            max_adjustments: Maximum number of price adjustments
        """

        assert self.ib is not None, "IB connection is not initialized"
        try:
            # Create combo contract for the spread
            from ib_async import ComboLeg, Contract as IBContract
            
            combo = IBContract()
            combo.symbol = "SPX"
            combo.secType = "BAG"
            combo.currency = "USD"
            combo.exchange = "SMART"
            
            # Leg 1: Sell put (short)
            leg1 = ComboLeg()
            leg1.conId = short_put.conId
            leg1.ratio = 1
            leg1.action = "SELL"
            leg1.exchange = "SMART"
            
            # Leg 2: Buy put (long)
            leg2 = ComboLeg()
            leg2.conId = long_put.conId
            leg2.ratio = 1
            leg2.action = "BUY"
            leg2.exchange = "SMART"
            
            combo.comboLegs = [leg1, leg2]
            
            # Create limit order with bot instance name as prefix
            order = LimitOrder("BUY", quantity, limit_price)  # BUY the spread (net credit)
            order.orderRef = f"{self.bot_id}_bull_put_spread"
            
            self.logger.info(f"Placing spread order: {quantity} @ {limit_price:.2f}")
            
            # Place order
            trade = self.ib.placeOrder(combo, order)
            self._active_order = trade
            
            # Monitor for fill with price adjustments
            adjustments_made = 0
            
            while adjustments_made <= max_adjustments:
                # Wait 30 seconds for fill
                await asyncio.sleep(30)
                
                # Check order status
                if trade.orderStatus.status in ["Filled", "Cancelled"]:
                    break
                
                # If not filled and we can still adjust, reduce price by 0.05
                if adjustments_made < max_adjustments:
                    limit_price -= 0.05
                    adjustments_made += 1
                    
                    self.logger.info(f"Order not filled - adjusting price to {limit_price:.2f} (adjustment {adjustments_made}/{max_adjustments})")
                    
                    # Modify order
                    order.lmtPrice = limit_price
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
            else:
                self.logger.warning(f"Order not filled - final status: {final_status}")
            
            self._active_order = None
            
        except Exception as e:
            self.logger.error(f"Error placing spread order: {e}", exc_info=True)
    
    async def stop(self) -> None:
        """
        Stop the bull put spread bot.
        """
        self.logger.info("Stopping Bull Put Spread bot")
        self._stop_requested = True
        
        # Cancel any active orders
        if self._active_order and self.ib:
            try:
                self.ib.cancelOrder(self._active_order.order)
                self.logger.info("Cancelled active order")
            except Exception as e:
                self.logger.error(f"Error cancelling order: {e}", exc_info=True)
        
        # Don't disconnect - the shared connection is managed by the framework
        self.ib = None
        
        self.logger.info("Bull Put Spread bot stopped")
