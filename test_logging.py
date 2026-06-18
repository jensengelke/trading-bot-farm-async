#!/usr/bin/env python
"""
Test script to verify logging functionality.

This script creates a simple test to verify that the logging framework
is working correctly without requiring IB connection.
"""

import asyncio
from pathlib import Path

from framework.logging_config import initialize_logging, get_system_logger, get_bot_logger


async def test_logging():
    """Test the logging framework."""
    
    # Initialize logging for test instance
    print("Initializing logging for 'test' instance...")
    initialize_logging("test")
    
    # Get system logger
    system_logger = get_system_logger()
    
    # Test system logging at different levels
    print("\n=== Testing System Logger ===")
    system_logger.debug("This is a DEBUG message (only in trace log)")
    system_logger.info("This is an INFO message")
    system_logger.warning("This is a WARNING message")
    system_logger.error("This is an ERROR message")
    
    # Test bot logger
    print("\n=== Testing Bot Logger ===")
    bot_logger = get_bot_logger("test_bot")
    bot_logger.debug("Bot DEBUG message (only in trace log)")
    bot_logger.info("Bot INFO message")
    bot_logger.warning("Bot WARNING message")
    bot_logger.error("Bot ERROR message")
    
    # Test exception logging
    print("\n=== Testing Exception Logging ===")
    try:
        raise ValueError("This is a test exception")
    except Exception as e:
        bot_logger.error(f"Caught exception: {e}", exc_info=True)
    
    print("\n=== Logging Test Complete ===")
    print("\nCheck the following log files:")
    print("  logs/test/system.log")
    print("  logs/test/system-error.log")
    print("  logs/test/system-trace.log")
    print("  logs/test/test_bot.log")
    print("  logs/test/test_bot-error.log")
    print("  logs/test/test_bot-trace.log")
    
    # Verify log files exist
    log_dir = Path("logs/test")
    expected_files = [
        "system.log",
        "system-error.log",
        "system-trace.log",
        "test_bot.log",
        "test_bot-error.log",
        "test_bot-trace.log"
    ]
    
    print("\n=== Verifying Log Files ===")
    all_exist = True
    for filename in expected_files:
        filepath = log_dir / filename
        exists = filepath.exists()
        status = "✓" if exists else "✗"
        print(f"  {status} {filepath}")
        if not exists:
            all_exist = False
    
    if all_exist:
        print("\n✓ All log files created successfully!")
    else:
        print("\n✗ Some log files are missing!")
    
    return all_exist


if __name__ == "__main__":
    success = asyncio.run(test_logging())
    exit(0 if success else 1)
