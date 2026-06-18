from abc import ABC, abstractmethod
from typing import Any, Dict
import logging

from framework.logging_config import get_bot_logger
from framework.decorators import trace_all_methods


class BotBase(ABC):
    """
    Base class for all trading bots.
    
    Each bot implementation must inherit from this class and implement
    the lifecycle methods.
    """
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any):
        """
        Initialize the bot.
        
        Args:
            bot_id: Unique identifier for this bot instance (from config filename)
            config: Bot-specific configuration from the bot's YAML file
            system_config: System-wide configuration (SystemConfig instance)
        """
        self.bot_id = bot_id
        self.config = config
        self.system_config = system_config
        self.logger = get_bot_logger(bot_id)
    
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
