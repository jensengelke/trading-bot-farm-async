"""
Decorators for the trading bot framework.

Provides method tracing and other utility decorators.
"""

import functools
import inspect
import logging
from typing import Any, Callable, TypeVar, cast

from framework.logging_config import get_bot_logger, get_system_logger


F = TypeVar('F', bound=Callable[..., Any])


def trace_method(func: F) -> F:
    """
    Decorator that logs entry and exit of method invocations.
    
    Logs at DEBUG level with method name, arguments, and return values.
    Works with both sync and async methods.
    
    Args:
        func: Method to decorate
        
    Returns:
        Decorated method
    """
    @functools.wraps(func)
    async def async_wrapper(self, *args: Any, **kwargs: Any) -> Any:
        # Determine which logger to use
        if hasattr(self, 'bot_id'):
            logger = get_bot_logger(self.bot_id)
        else:
            logger = get_system_logger()
        
        # Format arguments for logging
        args_repr = [repr(a) for a in args]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        
        class_name = self.__class__.__name__
        method_name = func.__name__
        
        logger.debug(f"→ {class_name}.{method_name}({signature})")
        
        try:
            result = await func(self, *args, **kwargs)
            logger.debug(f"← {class_name}.{method_name} returned {result!r}")
            return result
        except Exception as e:
            logger.debug(f"← {class_name}.{method_name} raised {type(e).__name__}: {e}")
            raise
    
    @functools.wraps(func)
    def sync_wrapper(self, *args: Any, **kwargs: Any) -> Any:
        # Determine which logger to use
        if hasattr(self, 'bot_id'):
            logger = get_bot_logger(self.bot_id)
        else:
            logger = get_system_logger()
        
        # Format arguments for logging
        args_repr = [repr(a) for a in args]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        
        class_name = self.__class__.__name__
        method_name = func.__name__
        
        logger.debug(f"→ {class_name}.{method_name}({signature})")
        
        try:
            result = func(self, *args, **kwargs)
            logger.debug(f"← {class_name}.{method_name} returned {result!r}")
            return result
        except Exception as e:
            logger.debug(f"← {class_name}.{method_name} raised {type(e).__name__}: {e}")
            raise
    
    # Return appropriate wrapper based on whether function is async
    if inspect.iscoroutinefunction(func):
        return cast(F, async_wrapper)
    else:
        return cast(F, sync_wrapper)


def trace_all_methods(cls: type) -> type:
    """
    Class decorator that applies trace_method to all methods in a class.
    
    Automatically decorates all public methods (not starting with '_') with
    the trace_method decorator.
    
    Args:
        cls: Class to decorate
        
    Returns:
        Decorated class
    """
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        # Skip private methods and special methods
        if not name.startswith('_'):
            setattr(cls, name, trace_method(method))
    
    return cls
