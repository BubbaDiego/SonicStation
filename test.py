#!/usr/bin/env python3
import sqlite3

def drop_config_overrides(db_path: str):
    """Permanently drops the config_overrides table from the specified SQLite DB."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("DROP TABLE IF EXISTS config_overrides")
            conn.commit()
        print("config_overrides table dropped (if it existed).")
    except sqlite3.Error as e:
        print(f"Error dropping config_overrides table: {e}")

if __name__ == "__main__":
    DB_PATH = "mother_brain.db"  # <-- adjust this path as needed
    drop_config_overrides(DB_PATH)
