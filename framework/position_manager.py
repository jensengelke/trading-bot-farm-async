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

from framework.db_schema import (
    EXPOSED_POSITION_STATUSES,
    init_schema,
    register_bot_version,
    utc_now,
)


class VirtualPosition:
    """
    Represents a virtual position tracked by the bot.
    """
    
    def __init__(
        self,
        position_id: str,
        bot_id: str,
        status: str = "ENTRY_PENDING",
        underlying_symbol: str = "",
        quantity: int = 1,
        opening_order_ref: str = "",
        multiplier: float = 100.0,
        signed_entry_price: Optional[float] = None,
        underlying_entry_price: Optional[float] = None,
        opening_fill_time: Optional[str] = None,
        close_before_first_expiry: bool = False,
        first_expiry_cutoff: Optional[str] = None,
        soft_exit_state: Optional[str] = None,
        closing_fill_price: Optional[float] = None,
        closing_fill_time: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        bot_version_id: Optional[str] = None,
        legs: Optional[List[Dict[str, Any]]] = None,
        # Backward compatibility keyword args
        expiration: Optional[str] = None,
        legs_data: Optional[Dict[str, Any]] = None,
        tp_order_id: Optional[int] = None,
        sl_order_id: Optional[int] = None,
        exit_conditions: Optional[List[Dict[str, Any]]] = None,
        initial_greeks: Optional[Dict[str, float]] = None,
        opening_fill_price: Optional[float] = None,
    ):
        self.position_id = position_id
        self.bot_id = bot_id
        # Normalize old PENDING to ENTRY_PENDING
        self.status = "ENTRY_PENDING" if status == "PENDING" else status
        self.underlying_symbol = underlying_symbol
        self.quantity = quantity
        self.opening_order_ref = opening_order_ref
        self.multiplier = multiplier
        self.signed_entry_price = signed_entry_price if signed_entry_price is not None else opening_fill_price
        self.underlying_entry_price = underlying_entry_price
        self.opening_fill_time = opening_fill_time
        self.close_before_first_expiry = close_before_first_expiry
        self.first_expiry_cutoff = first_expiry_cutoff
        self.soft_exit_state = soft_exit_state
        self.closing_fill_price = closing_fill_price
        self.closing_fill_time = closing_fill_time
        self.created_at = created_at or utc_now()
        self.updated_at = updated_at or utc_now()
        self.bot_version_id = bot_version_id
        self.legs = legs if legs is not None else (legs_data.get("legs", []) if isinstance(legs_data, dict) else [])
        self._expiration = expiration
        self.tp_order_id = tp_order_id
        self.sl_order_id = sl_order_id
        self.exit_conditions = exit_conditions or []
        self.initial_greeks = initial_greeks or {}

    @property
    def expiration(self) -> str:
        if self.legs:
            expiries = [leg.get("expiry") or leg.get("expiration") or "" for leg in self.legs]
            valid_expiries = [e for e in expiries if e]
            if valid_expiries:
                return min(valid_expiries)
        return self._expiration or ""

    @expiration.setter
    def expiration(self, val: str) -> None:
        self._expiration = val

    @property
    def legs_data(self) -> Dict[str, Any]:
        return {"legs": self.legs or []}

    @legs_data.setter
    def legs_data(self, val: Any) -> None:
        if isinstance(val, dict):
            self.legs = val.get("legs", [])
        elif isinstance(val, list):
            self.legs = val

    @property
    def opening_fill_price(self) -> Optional[float]:
        return self.signed_entry_price

    @opening_fill_price.setter
    def opening_fill_price(self, val: Optional[float]) -> None:
        self.signed_entry_price = val



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
    
    def create_position(
        self, position: VirtualPosition, legs: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """Create a new virtual position and its legs in the database."""
        legs_to_insert = legs if legs is not None else (position.legs or [])
        now = utc_now()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""
                    INSERT INTO virtual_positions (
                        position_id, bot_id, bot_version_id, status, underlying_symbol,
                        quantity, multiplier, opening_order_ref, signed_entry_price,
                        underlying_entry_price, opening_fill_time, close_before_first_expiry,
                        first_expiry_cutoff, soft_exit_state, closing_fill_price,
                        closing_fill_time, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position.position_id,
                    position.bot_id,
                    position.bot_version_id,
                    position.status,
                    position.underlying_symbol,
                    position.quantity,
                    position.multiplier,
                    position.opening_order_ref,
                    position.signed_entry_price,
                    position.underlying_entry_price,
                    position.opening_fill_time,
                    1 if position.close_before_first_expiry else 0,
                    position.first_expiry_cutoff,
                    position.soft_exit_state,
                    position.closing_fill_price,
                    position.closing_fill_time,
                    position.created_at,
                    position.updated_at,
                ))

                for i, leg in enumerate(legs_to_insert):
                    leg_id = str(leg.get("leg_id") or leg.get("name") or f"leg_{i+1}")
                    con_id = int(leg.get("con_id") or 0)
                    ratio = int(leg.get("ratio") or 1)
                    action = str(leg.get("action") or ("SELL" if ratio < 0 else "BUY")).upper()
                    right = str(leg.get("right") or "CALL").upper()
                    strike = float(leg.get("strike") or 0.0)
                    expiry = str(leg.get("expiry") or leg.get("expiration") or "")
                    cutoff = leg.get("last_trade_cutoff")
                    status = str(leg.get("status") or "OPEN").upper()
                    filled_qty = int(leg.get("filled_quantity") or 0)
                    rem_qty = int(leg.get("remaining_quantity") or (position.quantity * abs(ratio)))

                    conn.execute("""
                        INSERT INTO position_legs (
                            position_id, leg_id, con_id, ratio, action, right, strike,
                            expiry, last_trade_cutoff, filled_quantity, remaining_quantity,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        position.position_id, leg_id, con_id, abs(ratio), action, right, strike,
                        expiry, cutoff, filled_qty, rem_qty, status, now, now
                    ))

                conn.commit()
                position.legs = legs_to_insert
                self.logger.info(f"Created virtual position: {position.position_id}")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error creating position: {e}", exc_info=True)
            return False
    
    def update_position(self, position: VirtualPosition) -> bool:
        """Update an existing virtual position."""
        try:
            position.updated_at = utc_now()
            conn = self._connect()
            try:
                conn.execute("""
                    UPDATE virtual_positions SET
                        bot_id = ?,
                        bot_version_id = ?,
                        status = ?,
                        underlying_symbol = ?,
                        quantity = ?,
                        multiplier = ?,
                        opening_order_ref = ?,
                        signed_entry_price = ?,
                        underlying_entry_price = ?,
                        opening_fill_time = ?,
                        close_before_first_expiry = ?,
                        first_expiry_cutoff = ?,
                        soft_exit_state = ?,
                        closing_fill_price = ?,
                        closing_fill_time = ?,
                        updated_at = ?
                    WHERE position_id = ?
                """, (
                    position.bot_id,
                    position.bot_version_id,
                    position.status,
                    position.underlying_symbol,
                    position.quantity,
                    position.multiplier,
                    position.opening_order_ref,
                    position.signed_entry_price,
                    position.underlying_entry_price,
                    position.opening_fill_time,
                    1 if position.close_before_first_expiry else 0,
                    position.first_expiry_cutoff,
                    position.soft_exit_state,
                    position.closing_fill_price,
                    position.closing_fill_time,
                    position.updated_at,
                    position.position_id,
                ))
                conn.commit()
                self.logger.debug(f"Updated virtual position: {position.position_id}")
                return True
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error updating position: {e}", exc_info=True)
            return False

    def get_position(self, position_id: str) -> Optional[VirtualPosition]:
        """Get a virtual position by ID with its legs and protective orders."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM virtual_positions WHERE position_id = ?", (position_id,)
                ).fetchone()
            finally:
                conn.close()
            if row:
                pos = self._row_to_position(row)
                pos.legs = self.get_position_legs(position_id)
                orders = self.get_orders_for_position(position_id)
                for o in orders:
                    if o.get("role") == "takeprofit" and o.get("order_id"):
                        pos.tp_order_id = o["order_id"]
                    elif o.get("role") == "stoploss" and o.get("order_id"):
                        pos.sl_order_id = o["order_id"]
                return pos
            return None
        except Exception as e:
            self.logger.error(f"Error getting position: {e}", exc_info=True)
            return None

    def get_position_legs(self, position_id: str) -> List[Dict[str, Any]]:
        """Get legs for a position."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM position_legs WHERE position_id = ? ORDER BY leg_id",
                (position_id,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    
    def get_open_positions(self, bot_id: Optional[str] = None) -> List[VirtualPosition]:
        """Get all positions currently in exposed states."""
        try:
            conn = self._connect()
            try:
                placeholders = ",".join("?" for _ in EXPOSED_POSITION_STATUSES)
                if bot_id:
                    cursor = conn.execute(
                        f"SELECT * FROM virtual_positions WHERE status IN ({placeholders}) AND bot_id = ? ORDER BY created_at DESC",
                        list(EXPOSED_POSITION_STATUSES) + [bot_id],
                    )
                else:
                    cursor = conn.execute(
                        f"SELECT * FROM virtual_positions WHERE status IN ({placeholders}) ORDER BY created_at DESC",
                        list(EXPOSED_POSITION_STATUSES),
                    )
                rows = cursor.fetchall()
            finally:
                conn.close()
            positions = []
            for row in rows:
                pos = self._row_to_position(row)
                pos.legs = self.get_position_legs(pos.position_id)
                positions.append(pos)
            return positions
        except Exception as e:
            self.logger.error(f"Error getting open positions: {e}", exc_info=True)
            return []

    def get_active_position_count(self, bot_id: str) -> int:
        """Count virtual positions currently in exposed states for bot_id."""
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in EXPOSED_POSITION_STATUSES)
            row = conn.execute(
                f"SELECT COUNT(*) FROM virtual_positions WHERE bot_id = ? AND status IN ({placeholders})",
                [bot_id] + list(EXPOSED_POSITION_STATUSES)
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def consume_entry_latch(
        self, bot_id: str, trading_date: str, trigger_reason: str = "", position_id: Optional[str] = None
    ) -> bool:
        """Atomically persist an entry latch. Returns True if latched, False if already consumed."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO entry_latches (bot_id, trading_date, triggered_at, trigger_reason, position_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (bot_id, trading_date, utc_now(), trigger_reason, position_id)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def is_entry_latched(self, bot_id: str, trading_date: str) -> bool:
        """Check if entry latch exists for (bot_id, trading_date)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM entry_latches WHERE bot_id = ? AND trading_date = ?",
                (bot_id, trading_date)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_position_by_order_ref(self, order_ref: str) -> Optional[VirtualPosition]:
        """Get a virtual position by opening order reference."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM virtual_positions WHERE opening_order_ref = ?",
                    (order_ref,)
                ).fetchone()
            finally:
                conn.close()
            if row:
                pos = self._row_to_position(row)
                pos.legs = self.get_position_legs(pos.position_id)
                return pos
            return None
        except Exception as e:
            self.logger.error(f"Error getting position by order ref: {e}", exc_info=True)
            return None

    def _row_to_position(self, row: sqlite3.Row) -> VirtualPosition:
        """Convert a database row to a VirtualPosition object."""
        return VirtualPosition(
            position_id=row['position_id'],
            bot_id=row['bot_id'],
            status=row['status'],
            underlying_symbol=row['underlying_symbol'],
            quantity=row['quantity'],
            multiplier=row['multiplier'],
            opening_order_ref=row['opening_order_ref'],
            signed_entry_price=row['signed_entry_price'],
            underlying_entry_price=row['underlying_entry_price'],
            opening_fill_time=row['opening_fill_time'],
            close_before_first_expiry=bool(row['close_before_first_expiry']),
            first_expiry_cutoff=row['first_expiry_cutoff'],
            soft_exit_state=row['soft_exit_state'],
            closing_fill_price=row['closing_fill_price'],
            closing_fill_time=row['closing_fill_time'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            bot_version_id=row['bot_version_id'],
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

    _ORDER_UPDATABLE_COLUMNS = {
        "order_id", "perm_id", "client_id", "status", "status_message", "error_code",
        "filled_quantity", "avg_fill_price", "limit_price", "aux_price", "account",
        "oca_group", "oca_type", "trigger_method", "outside_rth", "reference_price",
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
            "order_id": None, "perm_id": None, "client_id": None, "account": None,
            "tif": None, "limit_price": None, "aux_price": None,
            "oca_group": None, "oca_type": None, "trigger_method": None,
            "outside_rth": 0, "reference_price": None,
            "modify_count": 0, "status_message": None, "error_code": None,
            "filled_quantity": 0, "avg_fill_price": None,
            "created_at": now, "updated_at": now,
            **order,
        }
        columns = [
            "order_ref", "position_id", "bot_id", "role", "order_id", "perm_id", "client_id",
            "account", "action", "order_type", "tif", "total_quantity", "limit_price", "aux_price",
            "oca_group", "oca_type", "trigger_method", "outside_rth", "reference_price",
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

    # ------------------------------------------------------------------
    # Order Events (Audit trail)
    # ------------------------------------------------------------------

    def record_order_event(self, event: Dict[str, Any]) -> bool:
        """Record an order event in the audit trail."""
        import uuid
        now = utc_now()
        row = {
            "event_id": event.get("event_id") or uuid.uuid4().hex,
            "order_ref": event["order_ref"],
            "position_id": event["position_id"],
            "bot_id": event["bot_id"],
            "event_type": event["event_type"],
            "status": event.get("status"),
            "filled_quantity": event.get("filled_quantity"),
            "remaining_quantity": event.get("remaining_quantity"),
            "avg_fill_price": event.get("avg_fill_price"),
            "limit_price": event.get("limit_price"),
            "aux_price": event.get("aux_price"),
            "error_code": event.get("error_code"),
            "message": event.get("message"),
            "timestamp": event.get("timestamp") or now,
        }
        cols = list(row.keys())
        try:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO order_events ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [row[c] for c in cols]
                )
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error recording order event: {e}", exc_info=True)
            return False

    def get_order_events(self, order_ref: str) -> List[Dict[str, Any]]:
        """Get audit events for an order, oldest first."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM order_events WHERE order_ref = ? ORDER BY timestamp ASC",
                    (order_ref,)
                ).fetchall()
            finally:
                conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            self.logger.error(f"Error getting order events for {order_ref}: {e}", exc_info=True)
            return []


