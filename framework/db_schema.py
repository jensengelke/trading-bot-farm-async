"""
Database schema v4.

Creates and manages the clean SQLite schema v4 shared by PositionManager, OrderService,
and the export/import tool.

Tables
------
bots               Configuration versions keyed by (bot_id, version_id).
entry_latches      Once-per-day floating entry latch keyed by (bot_id, trading_date).
virtual_positions  Virtual positions with strict lifecycle status CHECK constraint.
position_legs      Per-leg contract details, ratio, expiry, status, keyed by (position_id, leg_id).
orders             Framework-tracked orders keyed by unique order_ref.
fills              Execution fills reported by IB, keyed by exec_id.
order_events       Append-only audit trail of order events, keyed by UUID event_id.

PRAGMA user_version holds SCHEMA_VERSION (4).
Older database layouts (v1-v3) are rejected on open with a clear error; they are not
migrated in place.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

SCHEMA_VERSION = 4

ALLOWED_POSITION_STATUSES = (
    "ENTRY_PENDING",
    "OPEN",
    "EXIT_CANCEL_PENDING",
    "EXIT_WORKING",
    "PARTIALLY_EXPIRED",
    "NEEDS_RECONCILIATION",
    "CLOSED",
    "CANCELLED",
)

EXPOSED_POSITION_STATUSES = (
    "ENTRY_PENDING",
    "OPEN",
    "EXIT_CANCEL_PENDING",
    "EXIT_WORKING",
    "PARTIALLY_EXPIRED",
    "NEEDS_RECONCILIATION",
)

ALLOWED_LEG_STATUSES = (
    "OPEN",
    "EXPIRED",
    "EXERCISED",
    "ASSIGNED",
    "CLOSED",
)


def utc_now() -> str:
    """Current UTC time as a naive ISO string (the timestamp format used in the database)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def canonical_config_json(config: Dict[str, Any]) -> str:
    """Stable JSON text of a bot configuration (sorted keys, no whitespace)."""
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


def config_version_id(config: Dict[str, Any]) -> str:
    """Version id of a bot configuration: first 16 hex digits of the SHA-256 of its canonical JSON."""
    return hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()[:16]


_BOTS_DDL = """
    CREATE TABLE IF NOT EXISTS bots (
        bot_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        config TEXT NOT NULL,
        PRIMARY KEY (bot_id, version_id)
    )
"""

_ENTRY_LATCHES_DDL = """
    CREATE TABLE IF NOT EXISTS entry_latches (
        bot_id TEXT NOT NULL,
        trading_date TEXT NOT NULL,
        triggered_at TEXT NOT NULL,
        trigger_reason TEXT,
        position_id TEXT,
        PRIMARY KEY (bot_id, trading_date)
    )
"""

_VIRTUAL_POSITIONS_DDL = """
    CREATE TABLE IF NOT EXISTS virtual_positions (
        position_id TEXT PRIMARY KEY,
        bot_id TEXT NOT NULL,
        bot_version_id TEXT,
        status TEXT NOT NULL CHECK(status IN (
            'ENTRY_PENDING', 'OPEN', 'EXIT_CANCEL_PENDING', 'EXIT_WORKING',
            'PARTIALLY_EXPIRED', 'NEEDS_RECONCILIATION', 'CLOSED', 'CANCELLED'
        )),
        underlying_symbol TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        multiplier REAL NOT NULL DEFAULT 100,
        opening_order_ref TEXT NOT NULL,
        signed_entry_price REAL,
        underlying_entry_price REAL,
        opening_fill_time TEXT,
        close_before_first_expiry INTEGER NOT NULL DEFAULT 0,
        first_expiry_cutoff TEXT,
        soft_exit_state TEXT,
        closing_fill_price REAL,
        closing_fill_time TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (bot_id, bot_version_id) REFERENCES bots(bot_id, version_id)
    )
"""

_POSITION_LEGS_DDL = """
    CREATE TABLE IF NOT EXISTS position_legs (
        position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
        leg_id TEXT NOT NULL,
        con_id INTEGER NOT NULL,
        ratio INTEGER NOT NULL,
        action TEXT NOT NULL,
        right TEXT NOT NULL,
        strike REAL NOT NULL,
        expiry TEXT NOT NULL,
        last_trade_cutoff TEXT,
        filled_quantity INTEGER NOT NULL DEFAULT 0,
        remaining_quantity INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL CHECK(status IN ('OPEN', 'EXPIRED', 'EXERCISED', 'ASSIGNED', 'CLOSED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (position_id, leg_id)
    )
"""



_ORDERS_DDL = """
    CREATE TABLE IF NOT EXISTS orders (
        order_ref TEXT PRIMARY KEY,
        position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
        bot_id TEXT NOT NULL,
        role TEXT NOT NULL,
        order_id INTEGER,
        perm_id INTEGER,
        client_id INTEGER,
        account TEXT,
        action TEXT NOT NULL,
        order_type TEXT NOT NULL,
        tif TEXT,
        total_quantity REAL NOT NULL,
        limit_price REAL,
        aux_price REAL,
        oca_group TEXT,
        oca_type INTEGER,
        trigger_method INTEGER,
        outside_rth INTEGER NOT NULL DEFAULT 0,
        reference_price REAL,
        modify_count INTEGER NOT NULL DEFAULT 0,
        contract_json TEXT NOT NULL,
        status TEXT NOT NULL,
        status_message TEXT,
        error_code INTEGER,
        filled_quantity REAL NOT NULL DEFAULT 0,
        avg_fill_price REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

_FILLS_DDL = """
    CREATE TABLE IF NOT EXISTS fills (
        exec_id TEXT PRIMARY KEY,
        order_ref TEXT NOT NULL REFERENCES orders(order_ref),
        position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
        bot_id TEXT NOT NULL,
        perm_id INTEGER,
        order_id INTEGER,
        time TEXT NOT NULL,
        con_id INTEGER,
        sec_type TEXT,
        symbol TEXT,
        local_symbol TEXT,
        side TEXT NOT NULL,
        shares REAL NOT NULL,
        price REAL NOT NULL,
        cum_qty REAL,
        avg_price REAL,
        exchange TEXT,
        account TEXT,
        commission REAL,
        commission_currency TEXT,
        realized_pnl REAL,
        created_at TEXT NOT NULL
    )
"""

_ORDER_EVENTS_DDL = """
    CREATE TABLE IF NOT EXISTS order_events (
        event_id TEXT PRIMARY KEY,
        order_ref TEXT NOT NULL REFERENCES orders(order_ref),
        position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
        bot_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        status TEXT,
        filled_quantity REAL,
        remaining_quantity REAL,
        avg_fill_price REAL,
        limit_price REAL,
        aux_price REAL,
        error_code INTEGER,
        message TEXT,
        timestamp TEXT NOT NULL
    )
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bot_status ON virtual_positions(bot_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_status ON virtual_positions(status)",
    "CREATE INDEX IF NOT EXISTS idx_legs_position ON position_legs(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_bot_status ON orders(bot_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_fills_position ON fills(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_ref)",
    "CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_ref)",
    "CREATE INDEX IF NOT EXISTS idx_order_events_position ON order_events(position_id)",
]



def register_bot_version(
    conn: sqlite3.Connection, bot_id: str, config: Dict[str, Any], valid_from: str = None
) -> str:
    """Make sure the bots table has a row for this configuration and return its version id."""
    version_id = config_version_id(config)
    valid_from = valid_from or utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO bots (bot_id, version_id, valid_from, config) VALUES (?, ?, ?, ?)",
        (bot_id, version_id, valid_from, canonical_config_json(config))
    )
    conn.execute(
        "UPDATE bots SET valid_from = ? WHERE bot_id = ? AND version_id = ? AND valid_from > ?",
        (valid_from, bot_id, version_id, valid_from)
    )
    return version_id


def init_schema(db_path: Union[str, Path], logger: logging.Logger) -> None:
    """
    Initialize SQLite schema v4 in db_path.

    Rejects older database layouts (< 4) with sqlite3.DatabaseError.
    Fresh databases are created with clean v4 schema.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]

        if user_version == SCHEMA_VERSION:
            return

        if user_version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"Database at {db_path} has newer schema version {user_version} > {SCHEMA_VERSION}."
            )

        if user_version > 0 or len(tables) > 0:
            raise sqlite3.DatabaseError(
                f"Database at {db_path} has incompatible schema version {user_version} < {SCHEMA_VERSION}. "
                f"Schema v4 does not support in-place migration of v1-v3 databases. "
                f"Please archive or delete older database files."
            )

        # Fresh database: create clean schema v4
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        try:
            conn.execute(_BOTS_DDL)
            conn.execute(_ENTRY_LATCHES_DDL)
            conn.execute(_VIRTUAL_POSITIONS_DDL)
            conn.execute(_POSITION_LEGS_DDL)
            conn.execute(_ORDERS_DDL)
            conn.execute(_FILLS_DDL)
            conn.execute(_ORDER_EVENTS_DDL)
            for statement in _INDEXES:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
            logger.info(f"Initialized clean database schema v4 at {db_path}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

