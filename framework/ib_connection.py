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
