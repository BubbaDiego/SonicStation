#!/usr/bin/env python3

import sqlite3
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "mother_brain.db")

def main():
    print("Dropping and recreating the 'system_vars' table in mother_brain.db ...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1) Drop existing 'system_vars' table
    print("Dropping table 'system_vars' if it exists...")
    cur.execute("DROP TABLE IF EXISTS system_vars;")
    conn.commit()

    # 2) Recreate table with the columns needed
    print("Creating new 'system_vars' table with your new variables (brokerage/wallet).")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_vars (
            id INTEGER PRIMARY KEY,
            last_update_time_positions DATETIME,
            last_update_positions_source TEXT,
            last_update_time_prices DATETIME,
            last_update_prices_source TEXT,
            total_brokerage_balance REAL NOT NULL DEFAULT 0.0,
            total_wallet_balance REAL NOT NULL DEFAULT 0.0,
            total_balance REAL NOT NULL DEFAULT 0.0
        )
    """)

    # 3) Insert row with id=1
    print("Inserting default row with ID=1 ...")
    cur.execute("""
        INSERT INTO system_vars (
            id,
            last_update_time_positions,
            last_update_positions_source,
            last_update_time_prices,
            last_update_prices_source,
            total_brokerage_balance,
            total_wallet_balance,
            total_balance
        ) VALUES (1, NULL, NULL, NULL, NULL, 0.0, 0.0, 0.0)
    """)

    conn.commit()
    conn.close()
    print("system_vars table has been recreated successfully!")

if __name__ == "__main__":
    main()
