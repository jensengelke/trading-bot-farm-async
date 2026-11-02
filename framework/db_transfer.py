"""
Database export / import

Moves the persisted trading state (bots, virtual positions, orders, fills) between machines,
e.g. from a home PC to a cloud server, as a plain text JSON file instead of the binary
SQLite file.

Export file (``trading-bot-farm-db-export``, format version 1)::

    {"format": ..., "format_version": 1, "schema_version": 3, "exported_at": ...,
     "tables": {"bots": [...], "virtual_positions": [...], "orders": [...], "fills": [...]}}

Rows are sorted by primary key, so exporting an unchanged database twice gives identical files.
A file whose name ends in ``.gz`` is gzip-compressed.

Import merges into the target database (created/migrated if needed) in ONE transaction:
either everything is imported or, on any error, nothing. Rows are matched by their natural keys,
which are globally unique by construction (position UUIDs, orderRefs that contain the position
id, IB execIds, bot id + config hash), so databases of different machines can be combined:

* rows missing in the target are inserted;
* identical rows are left alone;
* a row that exists on both sides with different content is a conflict, resolved by the
  ``on_conflict`` policy: ``newer`` (default; the row with the later ``updated_at`` wins, ties keep
  the target), ``keep`` (the target always wins) or ``overwrite`` (the file always wins);
* ``bots.valid_from`` always becomes the earlier of both dates; fills never change except that
  a missing commission is filled in.

Before changing an existing, non-empty database a consistent copy is saved next to it
(``<name>.pre-import-<timestamp>.db``) unless disabled.
"""

import gzip
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from framework.db_schema import SCHEMA_VERSION, init_schema, utc_now

FORMAT_NAME = "trading-bot-farm-db-export"
FORMAT_VERSION = 1

# (table, primary key columns), parents before children (foreign keys)
TABLES: List[Tuple[str, Tuple[str, ...]]] = [
    ("bots", ("bot_id", "version_id")),
    ("virtual_positions", ("position_id",)),
    ("orders", ("order_ref",)),
    ("fills", ("exec_id",)),
]

CONFLICT_POLICIES = ("newer", "keep", "overwrite")


class TransferError(Exception):
    """Export or import failed; the target database was not changed."""


def _open_text(path: Path, mode: str):
    if str(path).lower().endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8")
    return open(path, mode, encoding="utf-8", newline="\n")


def export_database(db_path: Union[str, Path], out_path: Union[str, Path],
                    logger: Optional[logging.Logger] = None) -> Dict[str, int]:
    """
    Write the contents of ``db_path`` to ``out_path``.

    Returns:
        Number of exported rows per table
    """
    logger = logger or logging.getLogger("db_transfer")
    db_path, out_path = Path(db_path), Path(out_path)
    if not db_path.is_file():
        raise TransferError(f"Database not found: {db_path}")

    init_schema(db_path, logger)  # a no-op for current databases; upgrades older layouts
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")  # one snapshot for all tables
        tables: Dict[str, List[Dict[str, Any]]] = {}
        for table, pk in TABLES:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY {', '.join(pk)}").fetchall()
            tables[table] = [dict(row) for row in rows]
        conn.execute("COMMIT")
    finally:
        conn.close()

    document = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": utc_now(),
        "tables": tables,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # the temp file keeps the real suffix, because ".gz" selects compression in _open_text
    temp_path = out_path.with_name(f".tmp-{os.getpid()}-{out_path.name}")
    try:
        with _open_text(temp_path, "w") as handle:
            json.dump(document, handle, indent=1, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, out_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    counts = {table: len(rows) for table, rows in tables.items()}
    logger.info(f"Exported {db_path} to {out_path}: {counts}")
    return counts


def read_export(path: Union[str, Path]) -> Dict[str, Any]:
    """Load and validate an export file."""
    path = Path(path)
    if not path.is_file():
        raise TransferError(f"Export file not found: {path}")
    try:
        with _open_text(path, "r") as handle:
            document = json.load(handle)
    except (ValueError, OSError) as e:
        raise TransferError(f"Cannot read export file {path}: {e}") from e
    if not isinstance(document, dict) or document.get("format") != FORMAT_NAME:
        raise TransferError(f"{path} is not a {FORMAT_NAME} file")
    if document.get("format_version") != FORMAT_VERSION:
        raise TransferError(
            f"Unsupported export format version {document.get('format_version')} "
            f"(this program reads version {FORMAT_VERSION})"
        )
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, int) or schema_version > SCHEMA_VERSION:
        raise TransferError(
            f"The file was written by a newer program (schema {schema_version}, "
            f"this program supports up to {SCHEMA_VERSION}); update this program first"
        )
    tables = document.get("tables")
    if not isinstance(tables, dict) or any(not isinstance(tables.get(t), list) for t, _ in TABLES):
        raise TransferError(f"{path} does not contain all tables: {[t for t, _ in TABLES]}")
    return document


@dataclass
class TableReport:
    inserted: int = 0
    updated: int = 0          # target row replaced by the file's row
    unchanged: int = 0        # identical in both, or only an earlier valid_from / commission merged
    kept: int = 0             # differed, target row kept


@dataclass
class ImportReport:
    tables: Dict[str, TableReport] = field(default_factory=dict)
    conflicts: List[str] = field(default_factory=list)
    backup_path: Optional[Path] = None
    dry_run: bool = False

    def summary(self) -> str:
        lines = ["DRY RUN - nothing was changed" if self.dry_run else "Import committed"]
        for table, _ in TABLES:
            r = self.tables.get(table, TableReport())
            lines.append(
                f"  {table:<18} inserted {r.inserted:>5}  updated {r.updated:>5}  "
                f"unchanged {r.unchanged:>5}  kept {r.kept:>5}"
            )
        if self.conflicts:
            lines.append(f"  {len(self.conflicts)} conflict(s):")
            lines.extend(f"    {c}" for c in self.conflicts)
        if self.backup_path:
            lines.append(f"  backup of the previous database: {self.backup_path}")
        return "\n".join(lines)


def _has_data(db_path: Path) -> bool:
    """True if the database file already contains rows of any exported table."""
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return False
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        for table, _ in TABLES:
            try:
                if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return True
            except sqlite3.OperationalError:
                continue  # table does not exist (older layout)
        return False
    finally:
        conn.close()


def _backup(db_path: Path) -> Path:
    stamp = utc_now().replace(":", "").replace("-", "").replace("T", "-")[:15]
    backup_path = db_path.with_name(f"{db_path.stem}.pre-import-{stamp}{db_path.suffix}")
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def _label(table: str, pk: Tuple[str, ...], row: Dict[str, Any]) -> str:
    return f"{table}[{', '.join(str(row[c]) for c in pk)}]"


def _wins(policy: str, incoming: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """Should the row from the file replace the existing target row?"""
    if policy == "overwrite":
        return True
    if policy == "keep":
        return False
    return str(incoming.get("updated_at") or "") > str(current.get("updated_at") or "")


def _merge_table(conn: sqlite3.Connection, table: str, pk: Tuple[str, ...],
                 rows: List[Dict[str, Any]], policy: str, report: ImportReport) -> None:
    stats = report.tables.setdefault(table, TableReport())
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    where = " AND ".join(f"{c} = ?" for c in pk)
    for incoming in rows:
        if not isinstance(incoming, dict) or any(c not in incoming for c in pk):
            raise TransferError(f"Malformed {table} row in the export file: {incoming!r}")
        incoming = {c: incoming.get(c) for c in columns}
        found = conn.execute(f"SELECT * FROM {table} WHERE {where}", [incoming[c] for c in pk])
        current_row = found.fetchone()
        if current_row is None:
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [incoming[c] for c in columns]
            )
            stats.inserted += 1
            continue

        current = dict(zip(columns, current_row))
        if current == incoming:
            stats.unchanged += 1
            continue
        label = _label(table, pk, incoming)

        if table == "bots":
            # same (bot, config hash) = same configuration; only the first-use date can differ
            if incoming["valid_from"] < current["valid_from"]:
                conn.execute(f"UPDATE bots SET valid_from = ? WHERE {where}",
                             [incoming["valid_from"]] + [incoming[c] for c in pk])
            stats.unchanged += 1
            continue

        if table == "fills" and policy != "overwrite":
            # executions are immutable; take over a commission the target is still missing
            if current["commission"] is None and incoming["commission"] is not None:
                conn.execute(
                    "UPDATE fills SET commission = ?, commission_currency = ?, realized_pnl = ? "
                    "WHERE exec_id = ?",
                    (incoming["commission"], incoming["commission_currency"],
                     incoming["realized_pnl"], incoming["exec_id"])
                )
                stats.unchanged += 1
                continue
            report.conflicts.append(f"{label}: execution differs; target kept")
            stats.kept += 1
            continue

        if _wins(policy, incoming, current):
            assignments = ", ".join(f"{c} = ?" for c in columns if c not in pk)
            conn.execute(
                f"UPDATE {table} SET {assignments} WHERE {where}",
                [incoming[c] for c in columns if c not in pk] + [incoming[c] for c in pk]
            )
            stats.updated += 1
            report.conflicts.append(f"{label}: replaced by the file's row ({policy})")
        else:
            stats.kept += 1
            report.conflicts.append(f"{label}: kept the target row ({policy})")

def import_database(db_path: Union[str, Path], in_path: Union[str, Path],
                    on_conflict: str = "newer", dry_run: bool = False, backup: bool = True,
                    logger: Optional[logging.Logger] = None) -> ImportReport:
    """
    Merge the export file ``in_path`` into the database ``db_path`` (created if missing).

    All changes happen in one transaction: on any error nothing is imported. With
    ``dry_run`` the full merge is executed and then rolled back, so the report shows exactly
    what a real import would do.

    Do not import into a database that a running farm is using; stop the farm first.
    """
    logger = logger or logging.getLogger("db_transfer")
    if on_conflict not in CONFLICT_POLICIES:
        raise TransferError(f"on_conflict must be one of {CONFLICT_POLICIES}, not '{on_conflict}'")
    db_path = Path(db_path)
    document = read_export(in_path)  # validate before touching the target

    report = ImportReport(dry_run=dry_run)
    if backup and not dry_run and _has_data(db_path):
        report.backup_path = _backup(db_path)
        logger.info(f"Backed up {db_path} to {report.backup_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path.exists()
    try:
        init_schema(db_path, logger)
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table, pk in TABLES:
                    _merge_table(conn, table, pk, document["tables"][table], on_conflict, report)
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise TransferError(
                        f"The merged data would violate {len(violations)} foreign key(s), "
                        f"e.g. {tuple(violations[0])}; is the export file complete?"
                    )
                conn.execute("ROLLBACK" if dry_run else "COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    except TransferError:
        _remove_if_created(db_path, existed)
        raise
    except sqlite3.Error as e:
        _remove_if_created(db_path, existed)
        raise TransferError(f"Import failed, nothing was imported: {e}") from e
    if dry_run:
        _remove_if_created(db_path, existed)
    logger.info(report.summary())
    return report


def _remove_if_created(db_path: Path, existed: bool) -> None:
    """A failed or dry-run import must not leave an empty database behind."""
    if not existed and db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass
