from abc import ABC, abstractmethod
from typing import Any, Dict, TYPE_CHECKING
import logging

from framework.logging_config import get_bot_logger
from framework.decorators import trace_all_methods
from framework.request_tracker import get_request_tracker, get_error_dispatcher

if TYPE_CHECKING:
    from framework.ib_connection import IBConnectionManager


class BotBase(ABC):
    """
    Base class for all trading bots.
    
    Each bot implementation must inherit from this class and implement
    the lifecycle methods.
    """
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager: 'IBConnectionManager'):
        """
        Initialize the bot.
        
        Args:
            bot_id: Unique identifier for this bot instance (from config filename)
            config: Bot-specific configuration from the bot's YAML file
            system_config: System-wide configuration (SystemConfig instance)
            ib_connection_manager: Shared IB connection manager
        """
        self.bot_id = bot_id
        self.config = config
        self.system_config = system_config
        self.ib_connection_manager = ib_connection_manager
        self.logger = get_bot_logger(bot_id)
        
        # Get request tracker and error dispatcher
        self._request_tracker = get_request_tracker()
        self._error_dispatcher = get_error_dispatcher()
        
        # Register this bot's error handler
        self._error_dispatcher.register_bot_handler(self.bot_id, self._on_ib_error)
    
    def _on_ib_error(self, reqId: int, errorCode: int, errorString: str, contract) -> None:
        """
        Handle IB error events for this bot.
        
        This method can be overridden by subclasses to provide custom error handling.
        The default implementation logs the error.
        
        Args:
            reqId: Request ID or order ID
            errorCode: IB error code
            errorString: Error message
            contract: Contract the error applies to (or None)
        """
        # Default implementation: log the error
        if contract:
            self.logger.warning(f"IB Error [reqId={reqId}, code={errorCode}]: {errorString} | Contract: {contract}")
        else:
            self.logger.warning(f"IB Error [reqId={reqId}, code={errorCode}]: {errorString}")
    
    def track_request(self, req_id: int) -> None:
        """
        Track a request ID as belonging to this bot.
        
        Call this method after making an IB API call that returns a request ID.
        
        Args:
            req_id: The IB API request ID to track
        """
        self._request_tracker.register_request(req_id, self.bot_id)
    
    def untrack_request(self, req_id: int) -> None:
        """
        Stop tracking a request ID.
        
        Args:
            req_id: The IB API request ID to stop tracking
        """
        self._request_tracker.unregister_request(req_id)
    
    @abstractmethod
    async def start(self) -> None:
        """
        Start the bot.
        
        Called when the bot is started by the framework.
        Implement bot initialization and main logic here.
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """
        Stop the bot.
        
        Called when the bot is stopped by the framework.
        Implement cleanup logic here.
        """
        pass
    
    def cleanup(self) -> None:
        """
        Cleanup bot resources.
        
        Called by the framework during shutdown. Clears tracked requests
        and unregisters error handler.
        """
        # Clear all tracked requests for this bot
        self._request_tracker.clear_bot_requests(self.bot_id)
        
        # Unregister error handler
        self._error_dispatcher.unregister_bot_handler(self.bot_id)
