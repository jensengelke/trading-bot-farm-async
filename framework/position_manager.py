"""
Position Manager

Manages virtual positions using SQLite database for persistence.
Tracks opening fills, bracket orders, and position status.
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

from framework.db_schema import init_schema, register_bot_version, utc_now


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
        updated_at: Optional[str] = None,
        bot_version_id: Optional[str] = None
    ):
        self.position_id = position_id
        self.bot_id = bot_id
        self.status = status  # PENDING, OPEN, CLOSED, CANCELLED (opening order never filled)
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
        self.created_at = created_at or utc_now()
        self.updated_at = updated_at or utc_now()
        # Version of the bot's configuration this position was opened with (bots.version_id)
        self.bot_version_id = bot_version_id


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
        """Create the schema or upgrade an older layout (see framework/db_schema.py)."""
        try:
            init_schema(self.db_path, self.logger)
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
            conn = self._connect()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO virtual_positions (
                    position_id, bot_id, bot_version_id, status, underlying_symbol, expiration,
                    legs_data, quantity, opening_order_ref, opening_fill_price,
                    opening_fill_time, tp_order_id, sl_order_id, exit_conditions,
                    initial_greeks, closing_fill_price, closing_fill_time,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.position_id,
                position.bot_id,
                position.bot_version_id,
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
            position.updated_at = utc_now()
            
            conn = self._connect()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE virtual_positions SET
                    bot_id = ?,
                    bot_version_id = ?,
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
                position.bot_version_id,
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
            conn = self._connect()
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
            conn = self._connect()
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
            conn = self._connect()
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
            updated_at=row['updated_at'],
            bot_version_id=row['bot_version_id']
        )
    
    def _connect(self) -> sqlite3.Connection:
        """Open a connection with row access by name and foreign keys enforced."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    
    # Columns of the orders table that may be changed after the row is created
    _ORDER_UPDATABLE_COLUMNS = {
        "order_id", "perm_id", "client_id", "status", "status_message", "error_code",
        "filled_quantity", "avg_fill_price", "limit_price", "aux_price",
    }
    
    # ------------------------------------------------------------------
    # Bot versions
    # ------------------------------------------------------------------
    
    def register_bot_version(self, bot_id: str, config: Dict[str, Any]) -> str:
        """
        Store the bot's configuration as a version (once) and return its version id.
        
        The id is a hash of the configuration, so an unchanged configuration reuses its
        row (and its original valid_from) on every start, on every machine.
        """
        conn = self._connect()
        try:
            version_id = register_bot_version(conn, bot_id, config)
            conn.commit()
            return version_id
        finally:
            conn.close()
    
    def get_bot_version(self, bot_id: str, version_id: str) -> Optional[Dict[str, Any]]:
        """Get a bot version (bot_id, version_id, valid_from, config as dict) or None."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM bots WHERE bot_id = ? AND version_id = ?", (bot_id, version_id)
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            result = dict(row)
            result["config"] = json.loads(result["config"])
            return result
        except Exception as e:
            self.logger.error(f"Error getting bot version: {e}", exc_info=True)
            return None
    
    def get_bot_versions(self, bot_id: str) -> List[Dict[str, Any]]:
        """All versions of a bot, oldest first."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM bots WHERE bot_id = ? ORDER BY valid_from, version_id", (bot_id,)
                ).fetchall()
            finally:
                conn.close()
            result = []
            for row in rows:
                item = dict(row)
                item["config"] = json.loads(item["config"])
                result.append(item)
            return result
        except Exception as e:
            self.logger.error(f"Error getting bot versions: {e}", exc_info=True)
            return []
    
    def record_order(self, order: Dict[str, Any]) -> bool:
        """
        Insert a new order row.
        
        Args:
            order: Dict with the orders table columns (modify_count, filled_quantity,
                created_at and updated_at are defaulted when omitted)
            
        Returns:
            True if stored, False otherwise
        """
        now = utc_now()
        row = {
            "order_id": None, "perm_id": None, "client_id": None, "tif": None,
            "limit_price": None, "aux_price": None, "modify_count": 0,
            "status_message": None, "error_code": None,
            "filled_quantity": 0, "avg_fill_price": None,
            "created_at": now, "updated_at": now,
            **order,
        }
        columns = [
            "order_ref", "position_id", "bot_id", "role", "order_id", "perm_id", "client_id",
            "action", "order_type", "tif", "total_quantity", "limit_price", "aux_price",
            "modify_count", "contract_json", "status", "status_message", "error_code",
            "filled_quantity", "avg_fill_price", "created_at", "updated_at",
        ]
        try:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO orders ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    [row[c] for c in columns]
                )
                conn.commit()
            finally:
                conn.close()
            self.logger.info(f"Recorded order {row['order_ref']}")
            return True
        except Exception as e:
            self.logger.error(f"Error recording order: {e}", exc_info=True)
            return False
    
    def update_order(self, order_ref: str, **fields: Any) -> bool:
        """
        Update mutable columns of an order (see _ORDER_UPDATABLE_COLUMNS).
        
        Returns:
            True if a row was updated, False otherwise
        """
        invalid = set(fields) - self._ORDER_UPDATABLE_COLUMNS
        if invalid:
            raise ValueError(f"Cannot update order columns: {sorted(invalid)}")
        if not fields:
            return False
        try:
            assignments = ", ".join(f"{column} = ?" for column in fields)
            values = list(fields.values()) + [utc_now(), order_ref]
            conn = self._connect()
            try:
                cursor = conn.execute(
                    f"UPDATE orders SET {assignments}, updated_at = ? WHERE order_ref = ?",
                    values
                )
                conn.commit()
                return cursor.rowcount == 1
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error updating order {order_ref}: {e}", exc_info=True)
            return False
    
    def record_order_modification(
        self, order_ref: str, limit_price: Optional[float], aux_price: Optional[float]
    ) -> bool:
        """Store the new prices of a modified order and increment its modify_count."""
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE orders SET limit_price = ?, aux_price = ?, "
                    "modify_count = modify_count + 1, updated_at = ? WHERE order_ref = ?",
                    (limit_price, aux_price, utc_now(), order_ref)
                )
                conn.commit()
                return cursor.rowcount == 1
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error recording modification of {order_ref}: {e}", exc_info=True)
            return False
    
    def get_order(self, order_ref: str) -> Optional[Dict[str, Any]]:
        """Get an order by its unique orderRef, or None if unknown."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM orders WHERE order_ref = ?", (order_ref,)
                ).fetchone()
            finally:
                conn.close()
            return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"Error getting order {order_ref}: {e}", exc_info=True)
            return None
    
    def get_orders_for_position(self, position_id: str) -> List[Dict[str, Any]]:
        """Get all orders of a virtual position, oldest first."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE position_id = ? ORDER BY created_at, order_ref",
                    (position_id,)
                ).fetchall()
            finally:
                conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            self.logger.error(f"Error getting orders for {position_id}: {e}", exc_info=True)
            return []
    
    # ------------------------------------------------------------------
    # Fills
    # ------------------------------------------------------------------
    
    def record_fill(self, fill: Dict[str, Any]) -> bool:
        """
        Insert an execution. Duplicates (same exec_id) are ignored, so replays after a
        reconnect are harmless.
        
        Returns:
            True if the fill was newly stored, False if it already existed or on error
        """
        now = utc_now()
        row = {
            "perm_id": None, "order_id": None, "con_id": None, "sec_type": None,
            "symbol": None, "local_symbol": None, "cum_qty": None, "avg_price": None,
            "exchange": None, "account": None, "commission": None,
            "commission_currency": None, "realized_pnl": None, "created_at": now,
            **fill,
        }
        columns = [
            "exec_id", "order_ref", "position_id", "bot_id", "perm_id", "order_id", "time",
            "con_id", "sec_type", "symbol", "local_symbol", "side", "shares", "price",
            "cum_qty", "avg_price", "exchange", "account", "commission",
            "commission_currency", "realized_pnl", "created_at",
        ]
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO fills ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    [row[c] for c in columns]
                )
                conn.commit()
                inserted = cursor.rowcount == 1
            finally:
                conn.close()
            if inserted:
                self.logger.info(
                    f"Recorded fill {row['exec_id']} for {row['order_ref']}: "
                    f"{row['side']} {row['shares']} {row['local_symbol'] or row['symbol']} @ {row['price']}"
                )
            return inserted
        except Exception as e:
            self.logger.error(f"Error recording fill: {e}", exc_info=True)
            return False
    
    def update_fill_commission(
        self, exec_id: str, commission: Optional[float], currency: Optional[str],
        realized_pnl: Optional[float]
    ) -> bool:
        """Attach commission information to an existing fill."""
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE fills SET commission = ?, commission_currency = ?, realized_pnl = ? "
                    "WHERE exec_id = ?",
                    (commission, currency, realized_pnl, exec_id)
                )
                conn.commit()
                return cursor.rowcount == 1
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error updating commission of {exec_id}: {e}", exc_info=True)
            return False
    
    def get_fills_for_position(self, position_id: str) -> List[Dict[str, Any]]:
        """Get all executions of a virtual position, oldest first."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM fills WHERE position_id = ? ORDER BY time, exec_id",
                    (position_id,)
                ).fetchall()
            finally:
                conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            self.logger.error(f"Error getting fills for {position_id}: {e}", exc_info=True)
            return []

