"""
Export / import the trading database as a text file.

  python db_tool.py export data/live_positions.db live-export.json
  python db_tool.py import data/live_positions.db live-export.json --dry-run
  python db_tool.py import data/live_positions.db live-export.json

Use a name ending in .gz for a compressed file. Import merges into an existing database (see
framework/db_transfer.py for the rules); stop the farm before importing into its database.
"""

import argparse
import logging
import sys

from framework.db_transfer import (
    CONFLICT_POLICIES, TransferError, export_database, import_database
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export or import the trading database")
    sub = parser.add_subparsers(dest="command", required=True)

    export_parser = sub.add_parser("export", help="write a database to a JSON file")
    export_parser.add_argument("db_path", help="SQLite database to export")
    export_parser.add_argument("file", help="export file to write (.json or .json.gz)")

    import_parser = sub.add_parser("import", help="merge an export file into a database")
    import_parser.add_argument("db_path", help="SQLite database to merge into (created if missing)")
    import_parser.add_argument("file", help="export file to read (.json or .json.gz)")
    import_parser.add_argument(
        "--on-conflict", choices=CONFLICT_POLICIES, default="newer",
        help="rows that differ on both sides: 'newer' = later updated_at wins (default), "
             "'keep' = keep the database's row, 'overwrite' = take the file's row")
    import_parser.add_argument("--dry-run", action="store_true",
                               help="show what would happen, change nothing")
    import_parser.add_argument("--no-backup", action="store_true",
                               help="do not copy the existing database before importing")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        if args.command == "export":
            counts = export_database(args.db_path, args.file)
            print(f"Exported {args.db_path} to {args.file}")
            for table, count in counts.items():
                print(f"  {table:<18} {count:>6} rows")
        else:
            report = import_database(
                args.db_path, args.file, on_conflict=args.on_conflict,
                dry_run=args.dry_run, backup=not args.no_backup
            )
            print(report.summary())
    except TransferError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
