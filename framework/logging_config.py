"""
Logging configuration module for the trading bot framework.

Provides comprehensive logging functionality with:
- Instance-specific log directories (logs/{instance})
- Multiple log levels (error, info, trace)
- Log file rotation on startup
- Separate loggers for system and individual bots
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


class LoggingConfig:
    """
    Manages logging configuration for the trading bot framework.
    
    Creates and configures loggers for both system-level and bot-level logging,
    with automatic log rotation on startup.
    """
    
    def __init__(self, instance_name: str):
        """
        Initialize logging configuration.
        
        Args:
            instance_name: Name of the instance (e.g., 'live', 'paper')
        """
        self.instance_name = instance_name
        self.log_dir = Path("logs") / instance_name
        self.backup_dir: Optional[Path] = None
        
        # Create log directory if it doesn't exist
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Rotate existing logs
        self._rotate_logs()
        
        # Configure system logger
        self.system_logger = self._create_logger("system")
        
        # Track bot loggers
        self.bot_loggers = {}
    
    def _rotate_logs(self) -> None:
        """
        Rotate existing log files into a timestamped backup directory.
        
        Moves all existing .log files into logs/{instance}/backup-{timestamp}/
        """
        # Check if there are any log files to rotate
        log_files = list(self.log_dir.glob("*.log"))
        
        if not log_files:
            return
        
        # Create backup directory with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.backup_dir = self.log_dir / f"backup-{timestamp}"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Move all log files to backup directory
        for log_file in log_files:
            shutil.move(str(log_file), str(self.backup_dir / log_file.name))
        
        print(f"Rotated {len(log_files)} log file(s) to {self.backup_dir}")
    
    def _create_logger(self, name: str) -> logging.Logger:
        """
        Create a logger with three handlers (error, info, trace).
        
        Args:
            name: Logger name (e.g., 'system' or bot_id)
            
        Returns:
            Configured logger instance
        """
        logger = logging.getLogger(f"trading_bot_farm.{self.instance_name}.{name}")
        logger.setLevel(logging.DEBUG)  # Capture all levels
        
        # Remove existing handlers to avoid duplicates
        logger.handlers.clear()
        
        # Prevent propagation to root logger
        logger.propagate = False
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        simple_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 1. Error log (WARNING and above)
        error_handler = logging.FileHandler(self.log_dir / f"{name}-error.log", encoding='utf-8')
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(detailed_formatter)
        logger.addHandler(error_handler)
        
        # 2. Info log (INFO and above)
        info_handler = logging.FileHandler(self.log_dir / f"{name}.log", encoding='utf-8')
        info_handler.setLevel(logging.INFO)
        info_handler.setFormatter(simple_formatter)
        logger.addHandler(info_handler)
        
        # 3. Trace log (all levels)
        trace_handler = logging.FileHandler(self.log_dir / f"{name}-trace.log", encoding='utf-8')
        trace_handler.setLevel(logging.DEBUG)
        trace_handler.setFormatter(detailed_formatter)
        logger.addHandler(trace_handler)
        
        return logger
    
    def get_system_logger(self) -> logging.Logger:
        """
        Get the system logger.
        
        Returns:
            System logger instance
        """
        return self.system_logger
    
    def get_bot_logger(self, bot_id: str) -> logging.Logger:
        """
        Get or create a logger for a specific bot.
        
        Args:
            bot_id: Bot instance ID
            
        Returns:
            Bot-specific logger instance
        """
        if bot_id not in self.bot_loggers:
            self.bot_loggers[bot_id] = self._create_logger(bot_id)
        
        return self.bot_loggers[bot_id]
    
    def shutdown(self) -> None:
        """
        Shutdown all loggers and close file handlers.
        """
        # Shutdown system logger
        for handler in self.system_logger.handlers:
            handler.close()
        
        # Shutdown bot loggers
        for logger in self.bot_loggers.values():
            for handler in logger.handlers:
                handler.close()
        
        # Clear logger references
        self.bot_loggers.clear()


# Global logging config instance (initialized by main)
_logging_config: Optional[LoggingConfig] = None


def initialize_logging(instance_name: str) -> LoggingConfig:
    """
    Initialize the global logging configuration.
    
    Args:
        instance_name: Name of the instance (e.g., 'live', 'paper')
        
    Returns:
        LoggingConfig instance
    """
    global _logging_config
    _logging_config = LoggingConfig(instance_name)
    return _logging_config


def get_logging_config() -> Optional[LoggingConfig]:
    """
    Get the global logging configuration instance.
    
    Returns:
        LoggingConfig instance or None if not initialized
    """
    return _logging_config


def get_system_logger() -> logging.Logger:
    """
    Get the system logger.
    
    Returns:
        System logger instance
        
    Raises:
        RuntimeError: If logging not initialized
    """
    if _logging_config is None:
        raise RuntimeError("Logging not initialized. Call initialize_logging() first.")
    return _logging_config.get_system_logger()


def get_bot_logger(bot_id: str) -> logging.Logger:
    """
    Get a bot-specific logger.
    
    Args:
        bot_id: Bot instance ID
        
    Returns:
        Bot logger instance
        
    Raises:
        RuntimeError: If logging not initialized
    """
    if _logging_config is None:
        raise RuntimeError("Logging not initialized. Call initialize_logging() first.")
    return _logging_config.get_bot_logger(bot_id)
