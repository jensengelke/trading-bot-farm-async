"""
Database schema

Creates and migrates the SQLite schema shared by PositionManager and the export/import tool.

Tables
------
bots               one row per version of a bot's configuration. ``version_id`` is a hash of
                   the canonical config, so the same configuration gets the same id on every
                   machine (which is what makes databases mergeable). ``valid_from`` is when
                   the version was first used.
virtual_positions  one row per position opened by a bot; points to the bot version it was
                   opened with via the composite foreign key (bot_id, bot_version_id).
orders             every order placed by the framework, keyed by its unique orderRef.
fills              every execution of those orders, keyed by IB's execId.

``PRAGMA user_version`` holds SCHEMA_VERSION. Migrations are structural (they look at the
existing columns), so databases created by any earlier layout are upgraded in place:

  1  virtual_positions only
  2  + orders, fills, virtual_positions.bot_config (JSON copy of the config on every row)
  3  + bots; virtual_positions.bot_version_id replaces bot_config;
       orders.status_message / error_code (why IB rejected or cancelled an order)
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

SCHEMA_VERSION = 3

VIRTUAL_POSITION_COLUMNS = [
    "position_id", "bot_id", "bot_version_id", "status", "underlying_symbol", "expiration",
    "legs_data", "quantity", "opening_order_ref", "opening_fill_price", "opening_fill_time",
    "tp_order_id", "sl_order_id", "exit_conditions", "initial_greeks", "closing_fill_price",
    "closing_fill_time", "created_at", "updated_at",
]


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


def _virtual_positions_ddl(table: str) -> str:
    return f"""
        CREATE TABLE {table} (
            position_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            bot_version_id TEXT,
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
            updated_at TEXT NOT NULL,
            FOREIGN KEY (bot_id, bot_version_id) REFERENCES bots(bot_id, version_id)
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
        action TEXT NOT NULL,
        order_type TEXT NOT NULL,
        tif TEXT,
        total_quantity REAL NOT NULL,
        limit_price REAL,
        aux_price REAL,
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

# Executions reported by IB for framework orders, keyed by IB's execId. For combo orders IB
# may report the combo and/or its legs; con_id/sec_type identify which.
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

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bot_status ON virtual_positions(bot_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_status ON virtual_positions(status)",
    "CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_bot_status ON orders(bot_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_fills_position ON fills(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_ref)",
]


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def register_bot_version(
    conn: sqlite3.Connection, bot_id: str, config: Dict[str, Any], valid_from: str = None
) -> str:
    """
    Make sure the bots table has a row for this configuration and return its version id.

    The same configuration always yields the same version id. A configuration that was used
    before (also after switching to another one and back) keeps its original valid_from; an
    explicitly given earlier valid_from (migration of old rows) moves it back.
    """
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


def _rebuild_virtual_positions(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """
    Replace virtual_positions by the version-3 layout (bot_version_id + composite foreign key).

    Rows that carry a bot_config JSON copy get a bots row (valid_from = the position's
    created_at, so the earliest position using a config dates the version) and point to it.
    """
    old_columns = _columns(conn, "virtual_positions")
    has_config = "bot_config" in old_columns
    rows = conn.execute("SELECT * FROM virtual_positions").fetchall()

    conn.execute(_virtual_positions_ddl("virtual_positions_new"))
    for row in rows:
        values = dict(zip(old_columns, row))
        version_id = None
        raw_config = values.get("bot_config") if has_config else None
        if raw_config:
            try:
                config = json.loads(raw_config)
            except ValueError:
                config = None
            if config:
                version_id = register_bot_version(
                    conn, values["bot_id"], config, valid_from=values["created_at"]
                )
        values["bot_version_id"] = version_id
        conn.execute(
            f"INSERT INTO virtual_positions_new ({', '.join(VIRTUAL_POSITION_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in VIRTUAL_POSITION_COLUMNS)})",
            [values.get(column) for column in VIRTUAL_POSITION_COLUMNS]
        )
    conn.execute("DROP TABLE virtual_positions")
    conn.execute("ALTER TABLE virtual_positions_new RENAME TO virtual_positions")
    logger.info(f"Migrated virtual_positions to schema {SCHEMA_VERSION} ({len(rows)} rows)")


def init_schema(db_path: Union[str, Path], logger: logging.Logger) -> None:
    """Create the schema in ``db_path`` or upgrade an older layout in place (idempotent)."""
    conn = sqlite3.connect(db_path, isolation_level=None)  # explicit transaction below
    try:
        # Must be set outside a transaction; required while tables are rebuilt.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        try:
            conn.execute(_BOTS_DDL)
            if not _table_exists(conn, "virtual_positions"):
                conn.execute(_virtual_positions_ddl("virtual_positions"))
            elif "bot_version_id" not in _columns(conn, "virtual_positions"):
                _rebuild_virtual_positions(conn, logger)
            conn.execute(_ORDERS_DDL)
            conn.execute(_FILLS_DDL)
            order_columns = _columns(conn, "orders")
            if "status_message" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN status_message TEXT")
            if "error_code" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN error_code INTEGER")
            for statement in _INDEXES:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
