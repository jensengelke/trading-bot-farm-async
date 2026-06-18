import sys
from pathlib import Path
from typing import Any, Dict

# Add parent directory to path to import framework modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ib_async import IB
from ib_async.contract import Stock
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods


@trace_all_methods
class Bot(BotBase):
    """
    Verify bot implementation.
    
    Verifies successful connection to IB and prints 1 minute bars
    for configured symbols.
    """
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any):
        """
        Initialize the verify bot.
        
        Args:
            bot_id: Unique identifier for this bot instance
            config: Bot-specific configuration
            system_config: System-wide configuration
        """
        super().__init__(bot_id, config, system_config)
        self.ib = None
    
    async def start(self) -> None:
        """
        Start the verify bot.
        
        Connects to IB, retrieves historical data for configured symbols,
        and prints the last 15 bars for each symbol.
        """
        print(f"[{self.bot_id}] Starting verify bot")
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
        
        # Connect to IB
        self.ib = IB()
        print(f"[{self.bot_id}] Connecting to IB at {host}:{port} with client_id={client_id}")
        self.logger.info(f"Connecting to IB at {host}:{port} with client_id={client_id}")
        
        try:
            await self.ib.connectAsync(host, port, clientId=client_id)
            print(f"[{self.bot_id}] Connected to IB")
            self.logger.info("Connected to IB")
        except Exception as e:
            self.logger.error(f"Failed to connect to IB: {e}", exc_info=True)
            raise
        
        # Process each symbol
        for symbol_config in symbols:
            ticker = symbol_config.get("ticker")
            exchange = symbol_config.get("exchange", "SMART")
            currency = symbol_config.get("currency", "USD")
            
            print(f"\n[{self.bot_id}] Processing {ticker} ({exchange}, {currency})")
            self.logger.info(f"Processing {ticker} ({exchange}, {currency})")
            
            try:
                # Create contract
                contract = Stock(ticker, exchange, currency)
                
                # Request historical data
                bars = await self.ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr="1 D",
                    barSizeSetting="1 min",
                    whatToShow="TRADES",
                    useRTH=True
                )
                
                # Print last 15 bars
                print(f"[{self.bot_id}] Last 15 bars for {ticker}:")
                self.logger.info(f"Retrieved {len(bars)} bars for {ticker}")
                for bar in bars[-15:]:
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
        
        Disconnects from IB if connected.
        """
        print(f"[{self.bot_id}] Stopping verify bot")
        self.logger.info("Stopping verify bot")
        
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            print(f"[{self.bot_id}] Disconnected from IB")
            self.logger.info("Disconnected from IB")
        
        print(f"[{self.bot_id}] Verify bot stopped")
        self.logger.info("Verify bot stopped")
