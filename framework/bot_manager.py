import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List
import yaml

from framework.bot_base import BotBase
from framework.config.system_config import SystemConfig
from framework.logging_config import get_system_logger
from framework.decorators import trace_all_methods
from framework.ib_connection import get_ib_connection_manager


@trace_all_methods
class BotManager:
    """
    Manages the lifecycle of all bot instances.
    
    Discovers bot configuration files, instantiates bots, and manages
    their lifecycle (start/stop).
    """
    
    def __init__(self, config_dir: str):
        """
        Initialize the BotManager.
        
        Args:
            config_dir: Path to the configuration directory
        """
        self.config_dir = Path(config_dir).resolve()
        self.system_config = SystemConfig(config_dir)
        self.bots: Dict[str, BotBase] = {}
        self.bot_tasks: Dict[str, asyncio.Task] = {}
        self.logger = get_system_logger()
        self.ib_connection_manager = get_ib_connection_manager()
        
    def discover_bots(self) -> List[str]:
        """
        Discover all bot configuration files in the config directory.
        
        Returns:
            List of bot instance IDs (filenames without .yaml extension)
        """
        bot_configs = []
        
        # Find all YAML files except config.yaml and .secret-config.yaml
        for yaml_file in self.config_dir.glob("*.yaml"):
            if yaml_file.name not in ["config.yaml", ".secret-config.yaml"]:
                bot_id = yaml_file.stem
                bot_configs.append(bot_id)
        
        return bot_configs
    
    def load_bot_config(self, bot_id: str) -> Dict[str, Any]:
        """
        Load configuration for a specific bot.
        
        Args:
            bot_id: Bot instance ID
            
        Returns:
            Bot configuration dictionary
            
        Raises:
            FileNotFoundError: If bot config file doesn't exist
            ValueError: If 'type' field is missing
        """
        config_file = self.config_dir / f"{bot_id}.yaml"
        
        if not config_file.exists():
            raise FileNotFoundError(f"Bot config file not found: {config_file}")
        
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        if not config or 'type' not in config:
            raise ValueError(f"Bot config must have 'type' field: {config_file}")
        
        return config
    
    def load_bot_class(self, bot_type: str) -> type:
        """
        Dynamically load a bot class from bots/{bot_type}/bot.py
        
        Args:
            bot_type: Type of bot (from config 'type' field)
            
        Returns:
            Bot class
            
        Raises:
            ImportError: If bot module cannot be loaded
            AttributeError: If Bot class not found in module
        """
        # Construct path to bot module
        bot_module_path = Path(__file__).parent.parent / "bots" / bot_type / "bot.py"
        
        if not bot_module_path.exists():
            raise ImportError(f"Bot module not found: {bot_module_path}")
        
        # Load module dynamically
        spec = importlib.util.spec_from_file_location(f"bots.{bot_type}.bot", bot_module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module spec for: {bot_module_path}")
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"bots.{bot_type}.bot"] = module
        spec.loader.exec_module(module)
        
        # Get Bot class from module
        if not hasattr(module, 'Bot'):
            raise AttributeError(f"Module {bot_module_path} must define a 'Bot' class")
        
        return module.Bot
    
    def instantiate_bot(self, bot_id: str) -> BotBase:
        """
        Instantiate a bot from its configuration.
        
        Args:
            bot_id: Bot instance ID
            
        Returns:
            Instantiated bot
        """
        config = self.load_bot_config(bot_id)
        bot_type = config['type']
        
        bot_class = self.load_bot_class(bot_type)
        bot_instance = bot_class(bot_id, config, self.system_config, self.ib_connection_manager)
        
        return bot_instance
    
    async def start_bot(self, bot_id: str) -> None:
        """
        Start a specific bot.
        
        Args:
            bot_id: Bot instance ID
        """
        if bot_id in self.bots:
            msg = f"Bot {bot_id} is already running"
            print(msg)
            self.logger.warning(msg)
            return
        
        print(f"Starting bot: {bot_id}")
        self.logger.info(f"Starting bot: {bot_id}")
        
        try:
            bot = self.instantiate_bot(bot_id)
            self.bots[bot_id] = bot
            
            # Create task for bot's start method
            task = asyncio.create_task(bot.start())
            self.bot_tasks[bot_id] = task
            
            print(f"Bot {bot_id} started")
            self.logger.info(f"Bot {bot_id} started")
        except Exception as e:
            self.logger.error(f"Failed to start bot {bot_id}: {e}", exc_info=True)
            raise
    
    async def stop_bot(self, bot_id: str) -> None:
        """
        Stop a specific bot.
        
        Args:
            bot_id: Bot instance ID
        """
        if bot_id not in self.bots:
            msg = f"Bot {bot_id} is not running"
            print(msg)
            self.logger.warning(msg)
            return
        
        print(f"Stopping bot: {bot_id}")
        self.logger.info(f"Stopping bot: {bot_id}")
        bot = self.bots[bot_id]
        
        try:
            # Call bot's stop method
            await bot.stop()
            
            # Cancel the task if it's still running
            if bot_id in self.bot_tasks:
                task = self.bot_tasks[bot_id]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                del self.bot_tasks[bot_id]
            
            del self.bots[bot_id]
            print(f"Bot {bot_id} stopped")
            self.logger.info(f"Bot {bot_id} stopped")
        except Exception as e:
            self.logger.error(f"Error stopping bot {bot_id}: {e}", exc_info=True)
            raise
    
    async def start_all_bots(self) -> None:
        """
        Discover and start all bots in the configuration directory.
        """
        bot_ids = self.discover_bots()
        
        if not bot_ids:
            msg = "No bot configurations found"
            print(msg)
            self.logger.warning(msg)
            return
        
        msg = f"Found {len(bot_ids)} bot(s): {', '.join(bot_ids)}"
        print(msg)
        self.logger.info(msg)
        
        for bot_id in bot_ids:
            try:
                await self.start_bot(bot_id)
            except Exception as e:
                error_msg = f"Error starting bot {bot_id}: {e}"
                print(error_msg)
                self.logger.error(error_msg, exc_info=True)
    
    async def stop_all_bots(self) -> None:
        """
        Stop all running bots.
        """
        bot_ids = list(self.bots.keys())
        
        for bot_id in bot_ids:
            try:
                await self.stop_bot(bot_id)
            except Exception as e:
                print(f"Error stopping bot {bot_id}: {e}")
        
        # Disconnect shared IB connection
        await self.ib_connection_manager.disconnect()
    
    async def run(self) -> None:
        """
        Run the bot manager: start all bots and wait for them to complete.
        """
        await self.start_all_bots()
        
        # Wait for all bot tasks to complete
        if self.bot_tasks:
            await asyncio.gather(*self.bot_tasks.values(), return_exceptions=True)
