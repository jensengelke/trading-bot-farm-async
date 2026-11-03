"""
Unit tests for the database export / import (framework/db_transfer.py, db_tool.py).
"""

import gzip
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.db_schema import init_schema
from framework.db_transfer import (
    TransferError, export_database, import_database, read_export
)
from framework.position_manager import PositionManager
import db_tool

LOGGER = logging.getLogger("test_db_transfer")


def seed(db_path, bot_id="strategy_fkk", config=None, position_id="p1", ref_status="Filled",
         exec_id="E1", updated_at="2026-07-01T10:00:00", trading_date="2026-07-01"):
    """Create one version, entry latch, position, leg, order, fill and order event in ``db_path``."""
    pm = PositionManager(db_path, LOGGER)
    version_id = pm.register_bot_version(bot_id, config or {"dte": 0})
    conn = pm._connect()
    try:
        conn.execute(
            "INSERT INTO entry_latches (bot_id, trading_date, triggered_at, trigger_reason, position_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (bot_id, trading_date, "2026-07-01T08:59:00", "test trigger", position_id)
        )
        conn.execute(
            "INSERT INTO virtual_positions (position_id, bot_id, bot_version_id, status,"
            " underlying_symbol, quantity, multiplier, opening_order_ref, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (position_id, bot_id, version_id, "OPEN", "SPX", 1, 100.0,
             f"{bot_id}:{position_id}:open", "2026-07-01T09:00:00", updated_at))
        conn.execute(
            "INSERT INTO position_legs (position_id, leg_id, con_id, ratio, action, right, strike,"
            " expiry, filled_quantity, remaining_quantity, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (position_id, "leg1", 1001, 1, "BUY", "CALL", 5000.0, "20260702", 1, 0, "OPEN",
             "2026-07-01T09:00:00", updated_at)
        )
        conn.execute(
            "INSERT INTO orders (order_ref, position_id, bot_id, role, action, order_type,"
            " total_quantity, contract_json, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"{bot_id}:{position_id}:open", position_id, bot_id, "open", "BUY", "LMT", 1,
             "{}", ref_status, "2026-07-01T09:00:00", updated_at))
        conn.execute(
            "INSERT INTO fills (exec_id, order_ref, position_id, bot_id, time, side, shares, price,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (exec_id, f"{bot_id}:{position_id}:open", position_id, bot_id,
             "2026-07-01T09:01:00", "BOT", 1, -1.3, "2026-07-01T09:01:00"))
        conn.execute(
            "INSERT INTO order_events (event_id, order_ref, position_id, bot_id, event_type, status, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"evt_{exec_id}", f"{bot_id}:{position_id}:open", position_id, bot_id, "placed", "PendingSubmit", "2026-07-01T09:00:00")
        )
        conn.commit()
    finally:
        conn.close()
    return pm


def dump(db_path):
    """All rows of all tables, for comparing databases."""
    conn = sqlite3.connect(db_path)
    try:
        tables = ("bots", "entry_latches", "virtual_positions", "position_legs", "orders", "fills", "order_events")
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in tables
        }
    finally:
        conn.close()



class TransferTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return str(self.dir / name)


class TestExport(TransferTestCase):
    def test_export_contains_all_tables_and_is_deterministic(self):
        db = self.path("a.db")
        seed(db)
        counts = export_database(db, self.path("a.json"))
        self.assertEqual(
            counts,
            {
                "bots": 1,
                "entry_latches": 1,
                "virtual_positions": 1,
                "position_legs": 1,
                "orders": 1,
                "fills": 1,
                "order_events": 1,
            },
        )
        document = json.loads(Path(self.path("a.json")).read_text(encoding="utf-8"))
        self.assertEqual(document["format"], "trading-bot-farm-db-export")
        self.assertEqual(document["tables"]["fills"][0]["exec_id"], "E1")

        export_database(db, self.path("b.json"))
        first = json.loads(Path(self.path("a.json")).read_text(encoding="utf-8"))
        second = json.loads(Path(self.path("b.json")).read_text(encoding="utf-8"))
        first.pop("exported_at"), second.pop("exported_at")
        self.assertEqual(first, second)

    def test_export_does_not_modify_the_database(self):
        db = self.path("a.db")
        seed(db)
        before = dump(db)
        export_database(db, self.path("a.json"))
        self.assertEqual(dump(db), before)

    def test_gzip_round_trip(self):
        db = self.path("a.db")
        seed(db)
        export_database(db, self.path("a.json.gz"))
        with gzip.open(self.path("a.json.gz"), "rt", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["tables"]["orders"][0]["status"], "Filled")
        import_database(self.path("b.db"), self.path("a.json.gz"))
        self.assertEqual(dump(self.path("b.db")), dump(db))

    def test_missing_database_is_an_error(self):
        with self.assertRaises(TransferError):
            export_database(self.path("nope.db"), self.path("x.json"))

    def test_old_layout_is_rejected_before_export(self):
        db = self.path("old.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE virtual_positions (position_id TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            export_database(db, self.path("old.json"))
        self.assertIn("incompatible schema version", str(ctx.exception))



class TestImportIntoNewDatabase(TransferTestCase):
    def test_round_trip_reproduces_the_database(self):
        source = self.path("home.db")
        seed(source)
        export_database(source, self.path("e.json"))
        report = import_database(self.path("cloud.db"), self.path("e.json"))
        self.assertEqual(dump(self.path("cloud.db")), dump(source))
        self.assertEqual(report.tables["virtual_positions"].inserted, 1)
        self.assertIsNone(report.backup_path)  # nothing to back up

    def test_import_twice_changes_nothing(self):
        source = self.path("home.db")
        seed(source)
        export_database(source, self.path("e.json"))
        import_database(self.path("cloud.db"), self.path("e.json"))
        before = dump(self.path("cloud.db"))
        report = import_database(self.path("cloud.db"), self.path("e.json"))
        self.assertEqual(dump(self.path("cloud.db")), before)
        self.assertEqual(report.tables["fills"].inserted, 0)
        self.assertEqual(report.tables["fills"].unchanged, 1)
        self.assertEqual(report.conflicts, [])
        self.assertIsNotNone(report.backup_path)  # the target had data this time
        self.assertTrue(report.backup_path.exists())


class TestMerge(TransferTestCase):
    def status_of(self, name):
        conn = sqlite3.connect(self.path(name))
        try:
            return conn.execute("SELECT status FROM orders").fetchone()[0]
        finally:
            conn.close()

    def test_disjoint_databases_are_combined(self):
        seed(self.path("home.db"), position_id="p-home", exec_id="E-home")
        seed(self.path("cloud.db"), position_id="p-cloud", exec_id="E-cloud")
        export_database(self.path("home.db"), self.path("home.json"))
        report = import_database(self.path("cloud.db"), self.path("home.json"))
        merged = dump(self.path("cloud.db"))
        self.assertEqual({r[0] for r in merged["virtual_positions"]}, {"p-home", "p-cloud"})
        self.assertEqual({r[0] for r in merged["fills"]}, {"E-home", "E-cloud"})
        self.assertEqual(len(merged["bots"]), 1)  # same config -> same version on both machines
        self.assertEqual(report.tables["bots"].unchanged, 1)
        self.assertEqual(report.conflicts, [])

    def test_different_config_versions_of_one_bot_are_both_kept(self):
        seed(self.path("home.db"), config={"dte": 0}, position_id="p1", exec_id="E1")
        seed(self.path("cloud.db"), config={"dte": 7}, position_id="p2", exec_id="E2")
        export_database(self.path("home.db"), self.path("home.json"))
        import_database(self.path("cloud.db"), self.path("home.json"))
        self.assertEqual(len(dump(self.path("cloud.db"))["bots"]), 2)

    def test_bot_valid_from_becomes_the_earlier_date(self):
        seed(self.path("home.db"))
        seed(self.path("cloud.db"))
        for name, date in (("home.db", "2026-05-01T00:00:00"), ("cloud.db", "2026-06-01T00:00:00")):
            conn = sqlite3.connect(self.path(name))
            conn.execute("UPDATE bots SET valid_from = ?", (date,))
            conn.commit()
            conn.close()
        export_database(self.path("home.db"), self.path("home.json"))
        import_database(self.path("cloud.db"), self.path("home.json"))
        self.assertEqual(dump(self.path("cloud.db"))["bots"][0][2], "2026-05-01T00:00:00")

    def test_conflict_newer_takes_the_later_row(self):
        seed(self.path("home.db"), ref_status="Filled", updated_at="2026-07-02T10:00:00")
        seed(self.path("cloud.db"), ref_status="Submitted", updated_at="2026-07-01T10:00:00")
        export_database(self.path("home.db"), self.path("home.json"))
        report = import_database(self.path("cloud.db"), self.path("home.json"))
        self.assertEqual(self.status_of("cloud.db"), "Filled")
        self.assertEqual(report.tables["orders"].updated, 1)
        self.assertTrue(any("orders[strategy_fkk:p1:open]" in c for c in report.conflicts))

    def test_conflict_newer_keeps_the_target_when_the_file_is_older(self):
        seed(self.path("home.db"), ref_status="Submitted", updated_at="2026-07-01T10:00:00")
        seed(self.path("cloud.db"), ref_status="Filled", updated_at="2026-07-02T10:00:00")
        export_database(self.path("home.db"), self.path("home.json"))
        report = import_database(self.path("cloud.db"), self.path("home.json"))
        self.assertEqual(self.status_of("cloud.db"), "Filled")
        self.assertEqual(report.tables["orders"].kept, 1)

    def test_conflict_keep_never_changes_the_target(self):
        seed(self.path("home.db"), ref_status="Filled", updated_at="2026-07-02T10:00:00")
        seed(self.path("cloud.db"), ref_status="Submitted", updated_at="2026-07-01T10:00:00")
        export_database(self.path("home.db"), self.path("home.json"))
        import_database(self.path("cloud.db"), self.path("home.json"), on_conflict="keep")
        self.assertEqual(self.status_of("cloud.db"), "Submitted")

    def test_conflict_overwrite_always_takes_the_file(self):
        seed(self.path("home.db"), ref_status="Submitted", updated_at="2026-07-01T10:00:00")
        seed(self.path("cloud.db"), ref_status="Filled", updated_at="2026-07-02T10:00:00")
        export_database(self.path("home.db"), self.path("home.json"))
        import_database(self.path("cloud.db"), self.path("home.json"), on_conflict="overwrite")
        self.assertEqual(self.status_of("cloud.db"), "Submitted")

    def test_missing_commission_is_filled_in_but_fills_are_otherwise_immutable(self):
        seed(self.path("home.db"))
        seed(self.path("cloud.db"))
        for name, commission, price in (("home.db", 2.5, -1.3), ("cloud.db", None, -1.4)):
            conn = sqlite3.connect(self.path(name))
            conn.execute("UPDATE fills SET commission = ?, commission_currency = 'USD', price = ?",
                         (commission, price))
            conn.commit()
            conn.close()
        export_database(self.path("home.db"), self.path("home.json"))
        import_database(self.path("cloud.db"), self.path("home.json"))
        conn = sqlite3.connect(self.path("cloud.db"))
        try:
            row = conn.execute("SELECT commission, price FROM fills").fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (2.5, -1.4))  # commission taken over, price untouched


class TestSafety(TransferTestCase):
    def test_dry_run_reports_but_changes_nothing(self):
        seed(self.path("home.db"), position_id="p-home", exec_id="E-home")
        seed(self.path("cloud.db"), position_id="p-cloud", exec_id="E-cloud")
        export_database(self.path("home.db"), self.path("home.json"))
        before = dump(self.path("cloud.db"))
        report = import_database(self.path("cloud.db"), self.path("home.json"), dry_run=True)
        self.assertEqual(dump(self.path("cloud.db")), before)
        self.assertEqual(report.tables["virtual_positions"].inserted, 1)
        self.assertIsNone(report.backup_path)
        self.assertIn("DRY RUN", report.summary())
        self.assertEqual([p.name for p in self.dir.glob("*pre-import*")], [])

    def test_dry_run_does_not_create_the_target_database(self):
        seed(self.path("home.db"))
        export_database(self.path("home.db"), self.path("home.json"))
        import_database(self.path("new.db"), self.path("home.json"), dry_run=True)
        self.assertFalse(Path(self.path("new.db")).exists())

    def test_backup_is_a_usable_copy_of_the_previous_state(self):
        seed(self.path("home.db"), position_id="p-home", exec_id="E-home")
        seed(self.path("cloud.db"), position_id="p-cloud", exec_id="E-cloud")
        export_database(self.path("home.db"), self.path("home.json"))
        before = dump(self.path("cloud.db"))
        report = import_database(self.path("cloud.db"), self.path("home.json"))
        self.assertEqual(dump(str(report.backup_path)), before)
        no_backup = import_database(self.path("cloud.db"), self.path("home.json"), backup=False)
        self.assertIsNone(no_backup.backup_path)

    def test_failed_import_is_rolled_back_completely(self):
        seed(self.path("home.db"), position_id="p-home", exec_id="E-home")
        seed(self.path("cloud.db"), position_id="p-cloud", exec_id="E-cloud")
        export_database(self.path("home.db"), self.path("home.json"))
        document = json.loads(Path(self.path("home.json")).read_text(encoding="utf-8"))
        # an order whose position is missing from the file: violates the foreign key
        document["tables"]["virtual_positions"] = []
        Path(self.path("broken.json")).write_text(json.dumps(document), encoding="utf-8")
        before = dump(self.path("cloud.db"))
        with self.assertRaises(TransferError):
            import_database(self.path("cloud.db"), self.path("broken.json"))
        self.assertEqual(dump(self.path("cloud.db")), before)

    def test_failed_import_does_not_leave_a_new_database_behind(self):
        seed(self.path("home.db"))
        export_database(self.path("home.db"), self.path("home.json"))
        document = json.loads(Path(self.path("home.json")).read_text(encoding="utf-8"))
        document["tables"]["virtual_positions"] = []
        Path(self.path("broken.json")).write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(TransferError):
            import_database(self.path("new.db"), self.path("broken.json"))
        self.assertFalse(Path(self.path("new.db")).exists())

    def test_invalid_files_are_rejected_before_the_database_is_touched(self):
        seed(self.path("cloud.db"))
        before = dump(self.path("cloud.db"))
        Path(self.path("text.json")).write_text("not json", encoding="utf-8")
        Path(self.path("other.json")).write_text('{"format": "something else"}', encoding="utf-8")
        for name in ("text.json", "other.json", "missing.json"):
            with self.assertRaises(TransferError):
                import_database(self.path("cloud.db"), self.path(name))
        self.assertEqual(dump(self.path("cloud.db")), before)
        self.assertEqual([p.name for p in self.dir.glob("*pre-import*")], [])

    def test_file_from_a_newer_program_is_rejected(self):
        seed(self.path("home.db"))
        export_database(self.path("home.db"), self.path("home.json"))
        document = json.loads(Path(self.path("home.json")).read_text(encoding="utf-8"))
        document["schema_version"] = 999
        Path(self.path("future.json")).write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(TransferError):
            read_export(self.path("future.json"))

    def test_file_from_an_older_program_is_rejected(self):
        seed(self.path("home.db"))
        export_database(self.path("home.db"), self.path("home.json"))
        document = json.loads(Path(self.path("home.json")).read_text(encoding="utf-8"))
        document["schema_version"] = 3
        Path(self.path("past.json")).write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(TransferError) as ctx:
            read_export(self.path("past.json"))
        self.assertIn("Incompatible export file", str(ctx.exception))

    def test_unknown_conflict_policy_is_rejected(self):
        seed(self.path("home.db"))
        export_database(self.path("home.db"), self.path("home.json"))
        with self.assertRaises(TransferError):
            import_database(self.path("cloud.db"), self.path("home.json"), on_conflict="merge-ish")


class TestCommandLine(TransferTestCase):
    def test_export_then_import_via_cli(self):
        seed(self.path("home.db"))
        self.assertEqual(db_tool.main(["export", self.path("home.db"), self.path("e.json")]), 0)
        self.assertEqual(db_tool.main(["import", self.path("cloud.db"), self.path("e.json"),
                                       "--dry-run"]), 0)
        self.assertFalse(Path(self.path("cloud.db")).exists())
        self.assertEqual(db_tool.main(["import", self.path("cloud.db"), self.path("e.json"),
                                       "--on-conflict", "keep"]), 0)
        self.assertEqual(dump(self.path("cloud.db")), dump(self.path("home.db")))

    def test_errors_give_exit_code_1(self):
        self.assertEqual(db_tool.main(["export", self.path("nope.db"), self.path("e.json")]), 1)
        self.assertEqual(db_tool.main(["import", self.path("x.db"), self.path("nope.json")]), 1)


if __name__ == "__main__":
    unittest.main()
