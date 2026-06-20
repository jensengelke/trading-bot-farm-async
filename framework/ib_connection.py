"""
IB Connection Manager

Manages a shared IB connection that can be used by multiple bots.
"""

import asyncio
from typing import Optional
from ib_async import IB
import logging


class IBConnectionManager:
    """
    Manages a shared IB connection for all bots.
    
    Ensures only one connection is created and shared across all bot instances.
    """
    
    def __init__(self):
        """Initialize the connection manager."""
        self._ib: Optional[IB] = None
        self._connection_lock = asyncio.Lock()
        self._connected = False
        self._host: Optional[str] = None
        self._port: Optional[int] = None
        self._client_id: Optional[int] = None
        self._logger = logging.getLogger("system")
    
    async def connect(self, host: str, port: int, client_id: int) -> IB:
        """
        Get or create the shared IB connection.
        
        Args:
            host: IB Gateway/TWS host address
            port: IB Gateway/TWS port
            client_id: Client ID for the connection
            
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
                await self._ib.connectAsync(host, port, clientId=client_id)
                self._connected = True
                self._host = host
                self._port = port
                self._client_id = client_id
                self._logger.info(f"Successfully connected to IB at {host}:{port}")
                return self._ib
            except Exception as e:
                self._logger.error(f"Failed to connect to IB: {e}", exc_info=True)
                self._connected = False
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
    
    async def disconnect(self) -> None:
        """
        Disconnect the shared IB connection.
        
        Should only be called during framework shutdown.
        """
        async with self._connection_lock:
            if self._ib and self._ib.isConnected():
                self._logger.info("Disconnecting shared IB connection")
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
