"""
Position Manager

Manages virtual positions using SQLite database for persistence.
Tracks opening fills, bracket orders, and position status.
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging


class VirtualPosition:
    """
    Represents a virtual position tracked by the bot.
    """
    
    def __init__(
        self,
        position_id: str,
        bot_id: str,
        status: str,
        underlying_symbol: str,
        expiration: str,
        legs_data: Dict[str, Any],
        quantity: int,
        opening_order_ref: str,
        opening_fill_price: Optional[float] = None,
        opening_fill_time: Optional[str] = None,
        tp_order_id: Optional[int] = None,
        sl_order_id: Optional[int] = None,
        exit_conditions: Optional[List[Dict[str, Any]]] = None,
        initial_greeks: Optional[Dict[str, float]] = None,
        closing_fill_price: Optional[float] = None,
        closing_fill_time: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None
    ):
        self.position_id = position_id
        self.bot_id = bot_id
        self.status = status  # PENDING, OPEN, CLOSED
        self.underlying_symbol = underlying_symbol
        self.expiration = expiration
        self.legs_data = legs_data  # JSON serializable dict with leg details
        self.quantity = quantity
        self.opening_order_ref = opening_order_ref
        self.opening_fill_price = opening_fill_price
        self.opening_fill_time = opening_fill_time
        self.tp_order_id = tp_order_id
        self.sl_order_id = sl_order_id
        self.exit_conditions = exit_conditions or []
        self.initial_greeks = initial_greeks or {}
        self.closing_fill_price = closing_fill_price
        self.closing_fill_time = closing_fill_time
        self.created_at = created_at or datetime.utcnow().isoformat()
        self.updated_at = updated_at or datetime.utcnow().isoformat()


class PositionManager:
    """
    Manages virtual positions using SQLite database.
    """
    
    def __init__(self, db_path: str, logger: logging.Logger):
        """
        Initialize the position manager.
        
        Args:
            db_path: Path to SQLite database file
            logger: Logger instance
        """
        self.db_path = Path(db_path)
        self.logger = logger
        
        # Create parent directory if it doesn't exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize database schema if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create virtual_positions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS virtual_positions (
                    position_id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    underlying_symbol TEXT NOT NULL,
                    expiration TEXT NOT NULL,
                    legs_data TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    opening_order_ref TEXT NOT NULL,
                    opening_fill_price REAL,
                    opening_fill_time TEXT,
                    tp_order_id INTEGER,
                    sl_order_id INTEGER,
                    exit_conditions TEXT,
                    initial_greeks TEXT,
                    closing_fill_price REAL,
                    closing_fill_time TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_bot_status 
                ON virtual_positions(bot_id, status)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_status 
                ON virtual_positions(status)
            """)
            
            conn.commit()
            conn.close()
            
            self.logger.info(f"Database initialized at {self.db_path}")
            
        except Exception as e:
            self.logger.error(f"Error initializing database: {e}", exc_info=True)
            raise
    
    def create_position(self, position: VirtualPosition) -> bool:
        """
        Create a new virtual position in the database.
        
        Args:
            position: VirtualPosition object to create
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO virtual_positions (
                    position_id, bot_id, status, underlying_symbol, expiration,
                    legs_data, quantity, opening_order_ref, opening_fill_price,
                    opening_fill_time, tp_order_id, sl_order_id, exit_conditions,
                    initial_greeks, closing_fill_price, closing_fill_time,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.position_id,
                position.bot_id,
                position.status,
                position.underlying_symbol,
                position.expiration,
                json.dumps(position.legs_data),
                position.quantity,
                position.opening_order_ref,
                position.opening_fill_price,
                position.opening_fill_time,
                position.tp_order_id,
                position.sl_order_id,
                json.dumps(position.exit_conditions),
                json.dumps(position.initial_greeks),
                position.closing_fill_price,
                position.closing_fill_time,
                position.created_at,
                position.updated_at
            ))
            
            conn.commit()
            conn.close()
            
            self.logger.info(f"Created virtual position: {position.position_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error creating position: {e}", exc_info=True)
            return False
    
    def update_position(self, position: VirtualPosition) -> bool:
        """
        Update an existing virtual position.
        
        Args:
            position: VirtualPosition object with updated data
            
        Returns:
            True if successful, False otherwise
        """
        try:
            position.updated_at = datetime.utcnow().isoformat()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE virtual_positions SET
                    bot_id = ?,
                    status = ?,
                    underlying_symbol = ?,
                    expiration = ?,
                    legs_data = ?,
                    quantity = ?,
                    opening_order_ref = ?,
                    opening_fill_price = ?,
                    opening_fill_time = ?,
                    tp_order_id = ?,
                    sl_order_id = ?,
                    exit_conditions = ?,
                    initial_greeks = ?,
                    closing_fill_price = ?,
                    closing_fill_time = ?,
                    updated_at = ?
                WHERE position_id = ?
            """, (
                position.bot_id,
                position.status,
                position.underlying_symbol,
                position.expiration,
                json.dumps(position.legs_data),
                position.quantity,
                position.opening_order_ref,
                position.opening_fill_price,
                position.opening_fill_time,
                position.tp_order_id,
                position.sl_order_id,
                json.dumps(position.exit_conditions),
                json.dumps(position.initial_greeks),
                position.closing_fill_price,
                position.closing_fill_time,
                position.updated_at,
                position.position_id
            ))
            
            conn.commit()
            conn.close()
            
            self.logger.debug(f"Updated virtual position: {position.position_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating position: {e}", exc_info=True)
            return False
    
    def get_position(self, position_id: str) -> Optional[VirtualPosition]:
        """
        Get a virtual position by ID.
        
        Args:
            position_id: Position ID to retrieve
            
        Returns:
            VirtualPosition object or None if not found
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM virtual_positions WHERE position_id = ?
            """, (position_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return self._row_to_position(row)
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting position: {e}", exc_info=True)
            return None
    
    def get_open_positions(self, bot_id: Optional[str] = None) -> List[VirtualPosition]:
        """
        Get all open virtual positions.
        
        Args:
            bot_id: Optional bot ID to filter by
            
        Returns:
            List of VirtualPosition objects
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if bot_id:
                cursor.execute("""
                    SELECT * FROM virtual_positions 
                    WHERE status = 'OPEN' AND bot_id = ?
                    ORDER BY created_at DESC
                """, (bot_id,))
            else:
                cursor.execute("""
                    SELECT * FROM virtual_positions 
                    WHERE status = 'OPEN'
                    ORDER BY created_at DESC
                """)
            
            rows = cursor.fetchall()
            conn.close()
            
            return [self._row_to_position(row) for row in rows]
            
        except Exception as e:
            self.logger.error(f"Error getting open positions: {e}", exc_info=True)
            return []
    
    def get_position_by_order_ref(self, order_ref: str) -> Optional[VirtualPosition]:
        """
        Get a virtual position by opening order reference.
        
        Args:
            order_ref: Order reference to search for
            
        Returns:
            VirtualPosition object or None if not found
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM virtual_positions WHERE opening_order_ref = ?
            """, (order_ref,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return self._row_to_position(row)
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting position by order ref: {e}", exc_info=True)
            return None
    
    def _row_to_position(self, row: sqlite3.Row) -> VirtualPosition:
        """
        Convert a database row to a VirtualPosition object.
        
        Args:
            row: SQLite row object
            
        Returns:
            VirtualPosition object
        """
        return VirtualPosition(
            position_id=row['position_id'],
            bot_id=row['bot_id'],
            status=row['status'],
            underlying_symbol=row['underlying_symbol'],
            expiration=row['expiration'],
            legs_data=json.loads(row['legs_data']),
            quantity=row['quantity'],
            opening_order_ref=row['opening_order_ref'],
            opening_fill_price=row['opening_fill_price'],
            opening_fill_time=row['opening_fill_time'],
            tp_order_id=row['tp_order_id'],
            sl_order_id=row['sl_order_id'],
            exit_conditions=json.loads(row['exit_conditions']) if row['exit_conditions'] else [],
            initial_greeks=json.loads(row['initial_greeks']) if row['initial_greeks'] else {},
            closing_fill_price=row['closing_fill_price'],
            closing_fill_time=row['closing_fill_time'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
