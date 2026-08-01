"""
IB Connection Manager

Manages a shared IB connection that can be used by multiple bots.
"""

import asyncio
from typing import Optional
from ib_async import IB
import logging
from framework.request_tracker import get_error_dispatcher


from ib_async import IBC, Watchdog
from datetime import datetime
import pytz

class IBConnectionManager:
    """
    Manages a shared IB connection for all bots.
    
    Ensures only one connection is created and shared across all bot instances.
    """
    
    def __init__(self):
        """Initialize the connection manager."""
        self._ib: Optional[IB] = None
        self._watchdog: Optional[Watchdog] = None
        self._connection_lock = asyncio.Lock()
        self._connected = False
        self._host: Optional[str] = None
        self._port: Optional[int] = None
        self._client_id: Optional[int] = None
        self._logger = logging.getLogger("system")
        self._error_dispatcher = get_error_dispatcher()
        self._error_handler_registered = False
        self._maintenance_task: Optional[asyncio.Task] = None
        self._ibc_config = None
    
    async def connect(self, host: str, port: int, client_id: int, ibc_config=None) -> IB:
        """
        Get or create the shared IB connection, optionally using IBC and Watchdog.
        
        Args:
            host: IB Gateway/TWS host address
            port: IB Gateway/TWS port
            client_id: Client ID for the connection
            ibc_config: Optional dictionary or Pydantic object with IBC settings
            
        Returns:
            Shared IB connection instance
            
        Raises:
            Exception: If connection fails
        """
        async with self._connection_lock:
            # If already connected, return existing connection
            if self._connected and self._ib and self._ib.isConnected():
                self._logger.info(
                    f"Reusing existing IB connection to {self._host}:{self._port} "
                    f"with client_id={self._client_id}"
                )
                return self._ib
            
            # Create new connection
            self._logger.info(f"Creating new IB connection to {host}:{port} with client_id={client_id}")
            self._ib = IB()
            
            try:
                if ibc_config and getattr(ibc_config, 'enabled', False) or (isinstance(ibc_config, dict) and ibc_config.get('enabled')):
                    self._logger.info("IBC and Watchdog are enabled. Configuring Watchdog...")
                    
                    # Convert to dict if it's a pydantic model
                    config_dict = ibc_config if isinstance(ibc_config, dict) else ibc_config.dict()
                    
                    ibc = IBC(
                        twsVersion=config_dict.get('twsVersion', 1045),
                        gateway=config_dict.get('gateway', True),
                        tradingMode=config_dict.get('tradingMode', 'paper'),
                        userid=config_dict.get('userid', ''),
                        password=config_dict.get('password', ''),
                        twsPath=config_dict.get('twsPath', ''),
                        ibcPath=config_dict.get('ibcPath', ''),
                        ibcIni=config_dict.get('ibcIni', '')
                    )
                    
                    self._watchdog = Watchdog(
                        ibc, self._ib,
                        port=port,
                        clientId=client_id,
                        connectTimeout=10,
                        appStartupTime=30
                    )
                    
                    self._ibc_config = config_dict
                    
                    # Instead of just starting, we check if we're in the window
                    if self._is_in_maintenance_window():
                        self._watchdog.start()
                        
                        # Wait for connection to establish
                        timeout = 60
                        elapsed = 0
                        while not self._ib.isConnected() and elapsed < timeout:
                            await asyncio.sleep(1)
                            elapsed += 1
                            
                        if not self._ib.isConnected():
                            raise Exception("Watchdog failed to connect within timeout")
                    else:
                        self._logger.info("Outside of connection maintenance window. Connection will be established when window opens.")
                        
                    if not self._maintenance_task:
                        self._maintenance_task = asyncio.create_task(self._connection_maintenance_loop())
                else:
                    await self._ib.connectAsync(host, port, clientId=client_id)
                
                self._connected = True
                self._host = host
                self._port = port
                self._client_id = client_id
                
                # Register global error handler if not already registered
                if not self._error_handler_registered:
                    self._ib.errorEvent += self._on_ib_error
                    self._error_handler_registered = True
                    self._logger.info("Registered global error dispatcher")
                
                self._logger.info(f"Successfully connected to IB at {host}:{port}")
                return self._ib
            except Exception as e:
                self._logger.error(f"Failed to connect to IB: {e}", exc_info=True)
                self._connected = False
                if self._watchdog:
                    self._watchdog.stop()
                    self._watchdog = None
                self._ib = None
                raise
    
    def get_connection(self) -> Optional[IB]:
        """
        Get the current IB connection if it exists and is connected.
        
        Returns:
            IB connection instance or None if not connected
        """
        if self._connected and self._ib and self._ib.isConnected():
            return self._ib
        return None
    
    def _on_ib_error(self, reqId: int, errorCode: int, errorString: str, contract) -> None:
        """
        Global error handler that dispatches errors to the appropriate bot.
        
        Args:
            reqId: Request ID or order ID
            errorCode: IB error code
            errorString: Error message
            contract: Contract the error applies to (or None)
        """
        # Dispatch to the appropriate bot via the error dispatcher
        self._error_dispatcher.dispatch_error(reqId, errorCode, errorString, contract)
    
    async def disconnect(self) -> None:
        """
        Disconnect the shared IB connection.
        
        Should only be called during framework shutdown.
        """
        async with self._connection_lock:
            if self._watchdog:
                self._logger.info("Stopping Watchdog")
                self._watchdog.stop()
                self._watchdog = None
                
            if self._ib and self._ib.isConnected():
                self._logger.info("Disconnecting shared IB connection")
                
                # Unregister error handler
                if self._error_handler_registered:
                    self._ib.errorEvent -= self._on_ib_error
                    self._error_handler_registered = False
                
                self._ib.disconnect()
                self._connected = False
                self._logger.info("Disconnected from IB")
    
    def _is_in_maintenance_window(self) -> bool:
        if not self._ibc_config:
            return True
            
        trading_days = self._ibc_config.get('trading_days', ["Mon", "Tue", "Wed", "Thu", "Fri"])
        from_time = self._ibc_config.get('maintain_connection_from', "08:00")
        until_time = self._ibc_config.get('maintain_connection_until', "17:00")
        tz_str = self._ibc_config.get('maintain_connection_timezone', "America/New_York")
        
        try:
            tz = pytz.timezone(tz_str)
            now = datetime.now(tz)
            
            # Check day
            day_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            current_day = day_map[now.weekday()]
            if current_day not in trading_days:
                return False
                
            # Check time
            current_time = now.time()
            from_h, from_m = map(int, from_time.split(':'))
            until_h, until_m = map(int, until_time.split(':'))
            
            from_t = datetime.strptime(from_time, "%H:%M").time()
            until_t = datetime.strptime(until_time, "%H:%M").time()
            
            return from_t <= current_time <= until_t
        except Exception as e:
            self._logger.error(f"Error checking maintenance window: {e}")
            return True

    async def _connection_maintenance_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60) # Check every minute
                if not self._watchdog:
                    break
                    
                in_window = self._is_in_maintenance_window()
                
                if in_window:
                    if not self._ib.isConnected():
                        self._logger.info("In maintenance window but not connected. Starting watchdog...")
                        try:
                            self._watchdog.start()
                        except Exception as e:
                            self._logger.debug(f"Watchdog might already be running: {e}")
                else:
                    if self._ib.isConnected():
                        self._logger.info("Outside maintenance window, stopping watchdog and disconnecting.")
                        self._watchdog.stop()
                        self._ib.disconnect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in connection maintenance loop: {e}")

    def is_connected(self) -> bool:
        """
        Check if the connection is active.
        
        Returns:
            True if connected, False otherwise
        """
        return self._connected and self._ib is not None and self._ib.isConnected()


# Global singleton instance
_ib_connection_manager: Optional[IBConnectionManager] = None


def get_ib_connection_manager() -> IBConnectionManager:
    """
    Get the global IB connection manager instance.
    
    Returns:
        IBConnectionManager singleton instance
    """
    global _ib_connection_manager
    if _ib_connection_manager is None:
        _ib_connection_manager = IBConnectionManager()
    return _ib_connection_manager
