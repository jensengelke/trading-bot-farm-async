import asyncio
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path to import framework modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ib_async import IB, Contract
from ib_async.contract import Stock, Option, Future, Index
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods

# Import bot config schema
try:
    from bots.verify.config import VerifyBotConfig
except ImportError:
    # Fallback if running from different context
    from .config import VerifyBotConfig


@trace_all_methods
class Bot(BotBase):
    """
    Verify bot implementation.
    
    Verifies successful connection to IB and retrieves market data:
    - For stocks and futures: prints the latest 1-minute historical bar.
    - For options: finds the ATM call for this Friday and logs its bid/ask price.
    """
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager):
        """
        Initialize the verify bot.
        
        Args:
            bot_id: Unique identifier for this bot instance
            config: Bot-specific configuration (raw dict)
            system_config: System-wide configuration
            ib_connection_manager: Shared IB connection manager
        """
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        # Validate configuration using Pydantic model
        try:
            self.validated_config = VerifyBotConfig(**config)
            self.logger.info("Configuration validated successfully")
        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}", exc_info=True)
            raise ValueError(f"Invalid configuration for bot {bot_id}: {e}") from e
        
        self.ib = None
    
    async def _qualify_contract(self, contract: Contract, timeout: float = 5.0) -> Optional[Contract]:
        """
        Qualify a contract with a timeout to prevent hanging.
        
        Args:
            contract: Contract to qualify
            timeout: Timeout in seconds
            
        Returns:
            Qualified Contract if successful, None otherwise
        """
        assert self.ib is not None, "IB connection is not initialized"
        try:
            qualified = await asyncio.wait_for(
                self.ib.qualifyContractsAsync(contract),
                timeout=timeout
            )
            if qualified and qualified[0] and isinstance(qualified[0], Contract) and qualified[0].conId > 0:
                return qualified[0]
            return None
        except Exception as e:
            self.logger.debug(f"Contract qualification failed for {contract}: {e}")
            return None
    
    async def _resolve_underlying(
        self, ticker: str, exchange: str, currency: str, sec_type_hint: Optional[str] = None
    ) -> Contract:
        """
        Resolve and qualify the underlying contract for an option.
        
        Handles both stock and index underlyings (such as DAX on EUREX/DTB, SPX on CBOE).
        
        Args:
            ticker: Symbol ticker (e.g., 'TSLA', 'DAX', 'SPX')
            exchange: Exchange from config (e.g., 'SMART', 'EUREX', 'CBOE')
            currency: Currency (e.g., 'USD', 'EUR')
            sec_type_hint: Optional hint from config ('stock', 'index', 'future')
            
        Returns:
            Qualified underlying Contract
        """
        candidates: List[Contract] = []
        
        if sec_type_hint == "index":
            candidates = [
                Index(ticker, exchange, currency),
                Index(ticker, "", currency),
                Index(ticker, "DTB", currency),
                Index(ticker, "CBOE", currency),
            ]
        elif sec_type_hint == "stock":
            candidates = [
                Stock(ticker, exchange, currency),
                Stock(ticker, "SMART", currency),
            ]
        elif sec_type_hint == "future":
            candidates = [
                Future(ticker, exchange=exchange, currency=currency)
            ]
        else:
            # Auto-detect: if ticker/exchange suggests an index, prioritize Index
            is_likely_index = (
                ticker.upper() in ["DAX", "SPX", "NDX", "RUT", "VIX", "ESTX50", "SX5E", "XSP"]
                or exchange.upper() in ["CBOE", "EUREX", "DTB"]
            )
            if is_likely_index:
                candidates = [
                    Index(ticker, exchange, currency),
                    Index(ticker, "", currency),
                    Index(ticker, "DTB", currency),
                    Index(ticker, "CBOE", currency),
                    Stock(ticker, exchange, currency),
                    Stock(ticker, "SMART", currency),
                    Future(ticker, exchange=exchange, currency=currency),
                ]
            else:
                candidates = [
                    Stock(ticker, exchange, currency),
                    Stock(ticker, "SMART", currency),
                    Index(ticker, exchange, currency),
                    Index(ticker, "", currency),
                    Future(ticker, exchange=exchange, currency=currency),
                ]
        
        for candidate in candidates:
            self.logger.debug(f"Trying to qualify underlying candidate: {candidate}")
            qualified_contract = await self._qualify_contract(candidate, timeout=5.0)
            if qualified_contract:
                self.logger.info(
                    f"Resolved underlying {ticker} as {qualified_contract.secType} "
                    f"(conId={qualified_contract.conId}, exchange={qualified_contract.exchange})"
                )
                return qualified_contract
        
        raise ValueError(f"Could not qualify underlying contract for {ticker} ({exchange}, {currency})")


    def _find_target_friday_expiration(self, expirations: List[str]) -> str:
        """
        Find 'this Friday' expiration from available expirations (YYYYMMDD format).
        
        - If today is Mon-Fri, targets this Friday.
        - If today is Sat-Sun, targets next Friday.
        - If target Friday is not in expirations (e.g. holiday), selects closest upcoming Friday.
        - If no upcoming Friday exists, selects the earliest upcoming expiration.
        """
        if not expirations:
            raise ValueError("No expirations found in option chain")
        
        today = datetime.now().date()
        today_str = today.strftime("%Y%m%d")
        
        # Calculate target Friday (weekday 4)
        if today.weekday() <= 4:
            days_to_friday = 4 - today.weekday()
        else:
            days_to_friday = 4 - today.weekday() + 7
        
        this_friday = today + timedelta(days=days_to_friday)
        this_friday_str = this_friday.strftime("%Y%m%d")
        
        if this_friday_str in expirations:
            return this_friday_str
        
        # Filter for future expirations
        future_exps = sorted([exp for exp in expirations if exp >= today_str])
        if not future_exps:
            return sorted(expirations)[-1]
        
        # Find closest upcoming Friday expiration
        friday_exps = []
        for exp in future_exps:
            try:
                exp_date = datetime.strptime(exp, "%Y%m%d").date()
                if exp_date.weekday() == 4:
                    friday_exps.append(exp)
            except ValueError:
                continue
        
        if friday_exps:
            return friday_exps[0]
        
        # Fallback to closest future expiration
        return future_exps[0]

    async def _get_underlying_price(self, underlying: Contract) -> Optional[float]:
        """
        Get the current price (last, midpoint, close, or bid/ask mid) of the underlying contract.
        """
        assert self.ib is not None, "IB connection is not initialized"
        try:
            ticker = self.ib.reqMktData(underlying, "", False, False)
            price = None
            for _ in range(10):
                await asyncio.sleep(0.5)
                if ticker.last and ticker.last > 0 and not math.isnan(ticker.last):
                    price = ticker.last
                    break
                if ticker.midpoint() and ticker.midpoint() > 0 and not math.isnan(ticker.midpoint()):
                    price = ticker.midpoint()
                    break
                if ticker.close and ticker.close > 0 and not math.isnan(ticker.close):
                    price = ticker.close
                    break
                if ticker.bid and ticker.ask and ticker.bid > 0 and ticker.ask > 0:
                    price = (ticker.bid + ticker.ask) / 2.0
                    break
            
            self.ib.cancelMktData(underlying)
            return price
        except Exception as e:
            self.logger.warning(f"Error fetching underlying price for {underlying.symbol}: {e}")
            return None


    async def _create_option_contract(self, symbol_config: Dict[str, Any]) -> Contract:
        """
        Create and qualify an option contract for this Friday ATM call.
        """
        assert self.ib is not None, "IB connection is not initialized"
        
        ticker = symbol_config.get("ticker", "")
        exchange = symbol_config.get("exchange", "SMART")
        currency = symbol_config.get("currency", "USD")
        right = symbol_config.get("right", "C")
        sec_type_hint = symbol_config.get("security_type")
        
        if right not in ['C', 'P']:
            right = 'C'
        
        # 1. Resolve and qualify underlying contract
        underlying = await self._resolve_underlying(ticker, exchange, currency, sec_type_hint)
        
        # 2. Request option chains using qualified underlying contract ID
        self.logger.info(
            f"Requesting option chains for {underlying.symbol} (secType={underlying.secType}, conId={underlying.conId})"
        )
        try:
            chains = await asyncio.wait_for(
                self.ib.reqSecDefOptParamsAsync(
                    underlyingSymbol=underlying.symbol,
                    futFopExchange="",
                    underlyingSecType=underlying.secType,
                    underlyingConId=underlying.conId
                ),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timeout requesting option chains for {ticker}")
        except Exception as e:
            raise RuntimeError(f"Error requesting option chains for {ticker}: {e}") from e
        
        if not chains:
            raise ValueError(f"No option chains found for {ticker}")
        
        # Match chain with exchange if possible
        target_exchange = exchange.upper()
        matching_chains = [c for c in chains if c.exchange and c.exchange.upper() == target_exchange]
        chain = matching_chains[0] if matching_chains else chains[0]
        self.logger.info(f"Selected option chain: exchange={chain.exchange}, tradingClass={chain.tradingClass}")
        
        # 3. Find this Friday expiration
        expiration = self._find_target_friday_expiration(chain.expirations)
        self.logger.info(f"Selected expiration for {ticker}: {expiration}")
        
        # 4. Find ATM strike using underlying price
        strikes = sorted(chain.strikes)
        if not strikes:
            raise ValueError(f"No strikes found in option chain for {ticker}")
        
        underlying_price = await self._get_underlying_price(underlying)
        if underlying_price and underlying_price > 0:
            atm_strike = min(strikes, key=lambda s: abs(s - underlying_price))
            self.logger.info(f"Underlying {ticker} price is {underlying_price:.2f}, selected ATM strike {atm_strike}")
        else:
            atm_strike = strikes[len(strikes) // 2]
            self.logger.info(f"Could not determine underlying price for {ticker}, defaulting to middle strike {atm_strike}")
        
        # 5. Build Option contract
        option_exchange = chain.exchange or exchange
        option_contract = Option(
            symbol=ticker,
            lastTradeDateOrContractMonth=expiration,
            strike=atm_strike,
            right=right,
            exchange=option_exchange,
            currency=currency
        )
        if chain.tradingClass:
            option_contract.tradingClass = chain.tradingClass
        
        # 6. Qualify option contract
        self.logger.info(
            f"Qualifying option contract: {ticker} {expiration} {atm_strike} {right} ({option_exchange})"
        )
        qualified_opt = await self._qualify_contract(option_contract, timeout=8.0)
        if qualified_opt:
            self.logger.info(
                f"Qualified option contract: {qualified_opt.localSymbol or qualified_opt.symbol} (conId={qualified_opt.conId})"
            )
            return qualified_opt
        
        # Retry without tradingClass if qualification failed
        if getattr(option_contract, "tradingClass", None):
            option_contract.tradingClass = ""
            qualified_opt = await self._qualify_contract(option_contract, timeout=8.0)
            if qualified_opt:
                self.logger.info(
                    f"Qualified option contract without tradingClass: {qualified_opt.localSymbol or qualified_opt.symbol} (conId={qualified_opt.conId})"
                )
                return qualified_opt
        
        self.logger.warning(f"Could not qualify option contract for {ticker}, using constructed contract")
        return option_contract


    async def _create_contract(self, symbol_config: Dict[str, Any], security_type: str):
        """
        Create an IB contract based on security type.
        
        Args:
            symbol_config: Symbol configuration from bot config
            security_type: Type of security (stock, option, future)
            
        Returns:
            Contract object (Stock, Option, or Future)
        """
        ticker = symbol_config.get("ticker", "")
        exchange = symbol_config.get("exchange", "SMART")
        currency = symbol_config.get("currency", "USD")
        
        if not ticker:
            raise ValueError("Ticker symbol is required")
        
        if security_type == "stock":
            return Stock(ticker, exchange, currency)
        
        elif security_type == "option":
            if not self.ib:
                raise RuntimeError("IB connection not established")
            return await self._create_option_contract(symbol_config)
        
        elif security_type == "future":
            # For futures, we need to find the current month's contract
            if not self.ib:
                raise RuntimeError("IB connection not established")
            
            # Create a base contract to get available contracts
            base_contract = Future(ticker, exchange=exchange, currency=currency)
            
            # Get contract details
            contracts = await self.ib.reqContractDetailsAsync(base_contract)
            
            if not contracts:
                raise ValueError(f"No future contracts found for {ticker}")
            
            # Find the contract with the closest expiration
            now = datetime.now()
            closest_contract = min(
                contracts,
                key=lambda c: abs(
                    datetime.strptime(
                        getattr(c.contract, 'lastTradeDateOrContractMonth', None) or "19700101", 
                        "%Y%m%d"
                    ) - now
                )
            )
            
            expiration = getattr(closest_contract.contract, 'lastTradeDateOrContractMonth', None) or ""
                        
            if not expiration:
                raise ValueError(f"No expiration found for {ticker}")
            
            self.logger.info(
                f"Creating future contract: {ticker} {expiration}"
            )
            
            return Future(
                ticker,
                expiration,
                exchange,
                currency=currency
            )
        
        else:
            raise ValueError(f"Unknown security type: {security_type}")
    
    async def start(self) -> None:
        """
        Start the verify bot.
        
        Connects to IB, retrieves historical data for configured symbols,
        and prints the last 15 bars for each symbol.
        """
        self.logger.info("Starting verify bot")
        
        # Get connection parameters from system config
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        
        # Get symbols from bot config
        symbols = self.config.get("symbols", [])
        
        if not symbols:
            msg = "No symbols configured"
            self.logger.warning(msg)
            return
        
        # Get shared IB connection
        self.logger.info("Getting shared IB connection")
        
        try:
            ibc_config = self.system_config.get("ibc")
            self.ib = await self.ib_connection_manager.connect(host, port, client_id, ibc_config)
            self.logger.info("Using shared IB connection")
        except Exception as e:
            self.logger.error(f"Failed to get IB connection: {e}", exc_info=True)
            raise
        
        # Get security type from bot config (default to stock for backward compatibility)
        security_type = self.config.get("security_type", "stock")
        
        # Process each symbol
        for symbol_config in symbols:
            ticker = symbol_config.get("ticker")
            exchange = symbol_config.get("exchange", "SMART")
            currency = symbol_config.get("currency", "USD")
            
            self.logger.info(f"Processing {ticker} ({exchange}, {currency}) as {security_type}")
            
            try:
                # Create contract based on security type
                contract = await self._create_contract(symbol_config, security_type)
                
                if security_type == "option":
                    # Request market data for the option contract to get bid and ask prices
                    contract_desc = (
                        contract.localSymbol
                        or f"{contract.symbol} {contract.lastTradeDateOrContractMonth} {contract.strike} {contract.right}"
                    )
                    self.logger.info(f"Requesting market data for option: {contract_desc}")
                    ticker_data = self.ib.reqMktData(contract, "", False, False)
                    
                    bid = None
                    ask = None
                    for _ in range(10):
                        await asyncio.sleep(0.5)
                        if ticker_data.bid is not None and ticker_data.bid > 0 and not math.isnan(ticker_data.bid):
                            bid = ticker_data.bid
                        if ticker_data.ask is not None and ticker_data.ask > 0 and not math.isnan(ticker_data.ask):
                            ask = ticker_data.ask
                        if bid is not None and ask is not None:
                            break
                    
                    # If live bid/ask not yet set, capture whatever ticker reported
                    if bid is None and ticker_data.bid is not None:
                        bid = ticker_data.bid
                    if ask is None and ticker_data.ask is not None:
                        ask = ticker_data.ask
                    
                    output = f"[{self.bot_id}] Option {contract_desc}: Bid={bid}, Ask={ask}"
                    print(output)
                    self.logger.info(output)
                    
                    self.ib.cancelMktData(contract)
                else:
                    # Determine what to show based on security type
                    what_to_show = "TRADES" if security_type == "stock" else "MIDPOINT"
                    
                    # Request historical data
                    bars = await self.ib.reqHistoricalDataAsync(
                        contract,
                        endDateTime="",
                        durationStr="1 D",
                        barSizeSetting="1 min",
                        whatToShow=what_to_show,
                        useRTH=True
                    )
                    
                    # Print last bars
                    print(f"[{self.bot_id}] Last bars for {ticker}:")
                    self.logger.info(f"Retrieved {len(bars)} bars for {ticker}")
                    for bar in bars[-1:]:
                        print(
                            f"  {bar.date}  "
                            f"O={bar.open}  "
                            f"H={bar.high}  "
                            f"L={bar.low}  "
                            f"C={bar.close}  "
                            f"V={int(bar.volume)}"
                        )
            except Exception as e:
                self.logger.error(f"Error processing {ticker}: {e}", exc_info=True)
                raise
        
        print(f"\n[{self.bot_id}] Verify bot completed successfully")
        self.logger.info("Verify bot completed successfully")
    
    async def stop(self) -> None:
        """
        Stop the verify bot.
        
        Note: IB connection is shared and managed by the framework,
        so we don't disconnect here.
        """
        print(f"[{self.bot_id}] Stopping verify bot")
        self.logger.info("Stopping verify bot")
        
        # Don't disconnect - the shared connection is managed by the framework
        self.ib = None
        
        print(f"[{self.bot_id}] Verify bot stopped")
        self.logger.info("Verify bot stopped")
