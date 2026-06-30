"""
Request ID Tracker

Tracks which bot instance originated each IB API request, enabling targeted
error dispatching to the correct bot.
"""

import logging
from typing import Dict, Optional, Callable
from threading import Lock


class RequestTracker:
    """
    Tracks request IDs and their originating bot instances.
    
    This allows the framework to dispatch IB error events to the specific
    bot that made the request, rather than broadcasting to all bots.
    """
    
    def __init__(self):
        """Initialize the request tracker."""
        self._request_map: Dict[int, str] = {}  # reqId -> bot_id
        self._lock = Lock()
        self._logger = logging.getLogger("system")
    
    def register_request(self, req_id: int, bot_id: str) -> None:
        """
        Register a request ID as belonging to a specific bot.
        
        Args:
            req_id: The IB API request ID
            bot_id: The bot instance ID that made the request
        """
        with self._lock:
            self._request_map[req_id] = bot_id
            self._logger.debug(f"Registered reqId={req_id} for bot={bot_id}")
    
    def get_bot_for_request(self, req_id: int) -> Optional[str]:
        """
        Get the bot ID that originated a request.
        
        Args:
            req_id: The IB API request ID
            
        Returns:
            Bot ID if found, None otherwise
        """
        with self._lock:
            return self._request_map.get(req_id)
    
    def unregister_request(self, req_id: int) -> None:
        """
        Remove a request ID from tracking.
        
        Args:
            req_id: The IB API request ID to remove
        """
        with self._lock:
            if req_id in self._request_map:
                bot_id = self._request_map.pop(req_id)
                self._logger.debug(f"Unregistered reqId={req_id} from bot={bot_id}")
    
    def clear_bot_requests(self, bot_id: str) -> None:
        """
        Clear all requests for a specific bot (e.g., on bot shutdown).
        
        Args:
            bot_id: The bot instance ID
        """
        with self._lock:
            req_ids_to_remove = [req_id for req_id, bid in self._request_map.items() if bid == bot_id]
            for req_id in req_ids_to_remove:
                del self._request_map[req_id]
            if req_ids_to_remove:
                self._logger.debug(f"Cleared {len(req_ids_to_remove)} requests for bot={bot_id}")
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about tracked requests.
        
        Returns:
            Dictionary with statistics (total_requests, bots_with_requests)
        """
        with self._lock:
            bot_ids = set(self._request_map.values())
            return {
                "total_requests": len(self._request_map),
                "bots_with_requests": len(bot_ids)
            }


class ErrorDispatcher:
    """
    Dispatches IB error events to the appropriate bot based on request ID.
    """
    
    def __init__(self, request_tracker: RequestTracker):
        """
        Initialize the error dispatcher.
        
        Args:
            request_tracker: The request tracker instance
        """
        self._request_tracker = request_tracker
        self._bot_error_handlers: Dict[str, Callable] = {}  # bot_id -> error_handler
        self._lock = Lock()
        self._logger = logging.getLogger("system")
    
    def register_bot_handler(self, bot_id: str, error_handler: Callable) -> None:
        """
        Register an error handler for a specific bot.
        
        Args:
            bot_id: The bot instance ID
            error_handler: Callable that accepts (reqId, errorCode, errorString, contract)
        """
        with self._lock:
            self._bot_error_handlers[bot_id] = error_handler
            self._logger.debug(f"Registered error handler for bot={bot_id}")
    
    def unregister_bot_handler(self, bot_id: str) -> None:
        """
        Unregister an error handler for a specific bot.
        
        Args:
            bot_id: The bot instance ID
        """
        with self._lock:
            if bot_id in self._bot_error_handlers:
                del self._bot_error_handlers[bot_id]
                self._logger.debug(f"Unregistered error handler for bot={bot_id}")
    
    def dispatch_error(self, req_id: int, error_code: int, error_string: str, contract) -> None:
        """
        Dispatch an error to the appropriate bot.
        
        Args:
            req_id: The IB API request ID
            error_code: IB error code
            error_string: Error message
            contract: Contract the error applies to (or None)
        """
        # Find which bot made this request
        bot_id = self._request_tracker.get_bot_for_request(req_id)
        
        if bot_id:
            # Dispatch to specific bot
            with self._lock:
                handler = self._bot_error_handlers.get(bot_id)
            
            if handler:
                try:
                    handler(req_id, error_code, error_string, contract)
                    self._logger.debug(f"Dispatched error reqId={req_id} to bot={bot_id}")
                except Exception as e:
                    self._logger.error(f"Error in bot error handler for {bot_id}: {e}", exc_info=True)
            else:
                self._logger.warning(f"No error handler registered for bot={bot_id} (reqId={req_id})")
        else:
            # Request ID not tracked - this might be a system-level error or order-related
            # Log to system logger
            if contract:
                self._logger.warning(
                    f"IB Error [reqId={req_id}, code={error_code}]: {error_string} | "
                    f"Contract: {contract} (no bot association)"
                )
            else:
                self._logger.warning(
                    f"IB Error [reqId={req_id}, code={error_code}]: {error_string} (no bot association)"
                )


# Global singleton instances
_request_tracker: Optional[RequestTracker] = None
_error_dispatcher: Optional[ErrorDispatcher] = None


def get_request_tracker() -> RequestTracker:
    """
    Get the global request tracker instance.
    
    Returns:
        RequestTracker singleton instance
    """
    global _request_tracker
    if _request_tracker is None:
        _request_tracker = RequestTracker()
    return _request_tracker


def get_error_dispatcher() -> ErrorDispatcher:
    """
    Get the global error dispatcher instance.
    
    Returns:
        ErrorDispatcher singleton instance
    """
    global _error_dispatcher
    if _error_dispatcher is None:
        _error_dispatcher = ErrorDispatcher(get_request_tracker())
    return _error_dispatcher
