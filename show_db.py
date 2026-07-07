import sqlite3
import argparse
import sys

def show_database_info(db_path):
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = cursor.fetchall()
        
        if not tables:
            print(f"No tables found in database: {db_path}")
            return
            
        for table in tables:
            table_name = table['name']
            schema = table['sql']
            
            print("=" * 80)
            print(f"TABLE: {table_name}")
            print("-" * 80)
            print("SCHEMA:")
            print(schema)
            print("-" * 80)
            
            # Get contents
            cursor.execute(f"SELECT * FROM {table_name};")
            rows = cursor.fetchall()
            
            print(f"CONTENTS ({len(rows)} rows):")
            if rows:
                # Print headers
                headers = rows[0].keys()
                header_format = " | ".join(headers)
                print(header_format)
                print("-" * len(header_format))
                # Print rows
                for row in rows:
                    print(" | ".join(str(row[h]) for h in headers))
            else:
                print("Empty table")
            print("=" * 80)
            print()
            
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if 'conn' in locals() and conn is not None:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show schema and contents of a SQLite database")
    parser.add_argument("db_path", help="Path to the SQLite database file")
    args = parser.parse_args()
    
    show_database_info(args.db_path)
