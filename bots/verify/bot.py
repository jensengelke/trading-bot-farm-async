import sys
from pathlib import Path
from typing import Any, Dict

# Add parent directory to path to import framework modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ib_async import IB
from ib_async.contract import Stock, Option, Future
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from datetime import datetime, timedelta

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
    
    Verifies successful connection to IB and prints 1 minute bars
    for configured symbols.
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
            # For options, we need to find the closest expiration and ATM strike
            if not self.ib:
                raise RuntimeError("IB connection not established")
            
            # First, try to resolve the underlying contract to determine its security type
            # Try Stock first
            underlying_contract = Stock(ticker, exchange, currency)
            qualified = await self.ib.qualifyContractsAsync(underlying_contract)
            
            if qualified and qualified[0]:
                underlying_sec_type = getattr(qualified[0], 'secType', 'STK')
                self.logger.info(f"Resolved underlying {ticker} as {underlying_sec_type}")
            else:
                # If stock doesn't work, try Index
                from ib_async.contract import Index
                underlying_contract = Index(ticker, exchange, currency)
                qualified = await self.ib.qualifyContractsAsync(underlying_contract)
                
                if qualified and qualified[0]:
                    underlying_sec_type = getattr(qualified[0], 'secType', 'IND')
                    self.logger.info(f"Resolved underlying {ticker} as {underlying_sec_type}")
                else:
                    # If index doesn't work, try Future
                    underlying_contract = Future(ticker, exchange=exchange, currency=currency)
                    qualified = await self.ib.qualifyContractsAsync(underlying_contract)
                    
                    if qualified and qualified[0]:
                        underlying_sec_type = getattr(qualified[0], 'secType', 'FUT')
                        self.logger.info(f"Resolved underlying {ticker} as {underlying_sec_type}")
                    else:
                        # Fallback to STK if nothing works
                        underlying_sec_type = "STK"
                        self.logger.warning(
                            f"Could not resolve underlying for {ticker}, defaulting to {underlying_sec_type}"
                        )
            
            # Qualify the contract to get available chains
            chains = await self.ib.reqSecDefOptParamsAsync(
                ticker, exchange, underlying_sec_type, 0
            )
            
            if not chains:
                raise ValueError(f"No option chains found for {ticker}")
            
            # Get the first chain (usually the most liquid)
            chain = chains[0]
            
            # Find closest expiration
            expirations = sorted(chain.expirations)
            if not expirations:
                raise ValueError(f"No expirations found for {ticker}")
            
            closest_expiration = expirations[0]
            
            # Get strikes and find ATM
            strikes = sorted(chain.strikes)
            if not strikes:
                raise ValueError(f"No strikes found for {ticker}")
            
            # For simplicity, use middle strike as approximation of ATM
            # In production, you'd want to get current underlying price
            atm_strike = strikes[len(strikes) // 2]
            
            # Get the right (call or put) from config, default to both
            right = symbol_config.get("right", "C")  # Default to Call
            
            self.logger.info(
                f"Creating option contract: {ticker} {closest_expiration} "
                f"{atm_strike} {right}"
            )
            
            return Option(
                ticker,
                closest_expiration,
                atm_strike,
                right,
                exchange,
                currency=currency
            )
        
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
            print(f"[{self.bot_id}] {msg}")
            self.logger.warning(msg)
            return
        else:
            print(f"[{self.bot_id}] {len(symbols)} symbols configured for verification")
        
        # Get shared IB connection
        self.logger.info("Getting shared IB connection")
        
        try:
            self.ib = await self.ib_connection_manager.connect(host, port, client_id)
            print(f"[{self.bot_id}] Using shared IB connection")
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
            
            print(f"\n[{self.bot_id}] Processing {ticker} ({exchange}, {currency}) as {security_type}")
            self.logger.info(f"Processing {ticker} ({exchange}, {currency}) as {security_type}")
            
            try:
                # Create contract based on security type
                contract = await self._create_contract(symbol_config, security_type)
                
                # Determine what to show based on security type
                what_to_show = "TRADES"
                if security_type == "option":
                    what_to_show = "OPTION_IMPLIED_VOLATILITY"
                elif security_type == "future":
                    what_to_show = "MIDPOINT"
                
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
