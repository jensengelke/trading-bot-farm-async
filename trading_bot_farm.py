#!/usr/bin/env python
"""
Trading Bot Farm - Main Entry Point

Starts the trading bot framework with the specified configuration directory.
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from framework.bot_manager import BotManager
from framework.logging_config import initialize_logging, get_logging_config, get_system_logger


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Trading Bot Farm - Run multiple trading bots with shared configuration"
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        required=True,
        help="Path to configuration directory (e.g., config/live)"
    )
    return parser.parse_args()


async def main():
    """Main entry point for the trading bot farm."""
    args = parse_arguments()
    
    config_dir = args.config
    
    # Verify config directory exists
    config_path = Path(config_dir)
    if not config_path.exists():
        print(f"Error: Configuration directory does not exist: {config_dir}")
        sys.exit(1)
    
    # Extract instance name from config directory (e.g., 'live' from 'config/live')
    instance_name = config_path.name
    
    # Initialize logging
    initialize_logging(instance_name)
    logger = get_system_logger()
    
    print(f"Trading Bot Farm starting with config: {config_dir}")
    print("=" * 60)
    logger.info(f"Trading Bot Farm starting with config: {config_dir}")
    
    # Create bot manager
    bot_manager = BotManager(config_dir)
    
    # Set up signal handlers for graceful shutdown (Windows compatible)
    shutdown_event = asyncio.Event()
    
    def signal_handler(sig, frame):
        print(f"\nReceived signal, shutting down...")
        shutdown_event.set()
    
    # Register signal handlers (Windows compatible)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Run the bot manager with shutdown monitoring
        run_task = asyncio.create_task(bot_manager.run())
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        
        # Wait for either completion or shutdown signal
        done, pending = await asyncio.wait(
            [run_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cancel pending tasks
        for task in pending:
            task.cancel()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received, shutting down...")
        logger.info("Keyboard interrupt received, shutting down")
    except Exception as e:
        print(f"Error running bot manager: {e}")
        logger.error(f"Error running bot manager: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
    finally:
        # Ensure all bots are stopped
        await bot_manager.stop_all_bots()
        
        # Shutdown logging
        logging_config = get_logging_config()
        if logging_config:
            logging_config.shutdown()
        
        print("=" * 60)
        print("Trading Bot Farm stopped")
        logger.info("Trading Bot Farm stopped")


if __name__ == "__main__":
    # Run the async main function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
