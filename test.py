#!/usr/bin/env python3

import sqlite3
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "mother_brain.db")

def main():
    print("Dropping & Re-creating all relevant tables in mother_brain.db ...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ---------- 1) Drop all tables if they exist ----------
    tables = [
        "prices",
        "positions",
        "alerts",
        "api_status_counters",
        "wallets",
        "brokers",
        "system_vars",
    ]
    for tbl in tables:
        print(f"Dropping table if exists: {tbl}")
        cursor.execute(f"DROP TABLE IF EXISTS {tbl}")

    conn.commit()

    # ---------- 2) Create them fresh ----------

    # PRICES
    print("Creating 'prices' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id TEXT PRIMARY KEY,
            asset_type TEXT NOT NULL,
            current_price REAL NOT NULL,
            previous_price REAL NOT NULL DEFAULT 0.0,
            last_update_time DATETIME NOT NULL,
            previous_update_time DATETIME,
            source TEXT NOT NULL
        )
    """)

    # POSITIONS
    print("Creating 'positions' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            asset_type TEXT NOT NULL,
            position_type TEXT NOT NULL,
            entry_price REAL NOT NULL,
            liquidation_price REAL NOT NULL,
            current_travel_percent REAL NOT NULL DEFAULT 0.0,
            value REAL NOT NULL DEFAULT 0.0,
            collateral REAL NOT NULL,
            size REAL NOT NULL,
            wallet TEXT NOT NULL DEFAULT 'Default',
            leverage REAL DEFAULT 0.0,
            last_updated DATETIME NOT NULL,
            alert_reference_id TEXT,
            hedge_buddy_id TEXT,
            current_price REAL,
            liquidation_distance REAL,
            heat_index REAL NOT NULL DEFAULT 0.0,
            current_heat_index REAL NOT NULL DEFAULT 0.0,
            wallet_name TEXT NOT NULL DEFAULT 'Default'
        )
    """)

    # ALERTS
    print("Creating 'alerts' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'BTC',
            trigger_value REAL NOT NULL,
            condition TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            last_triggered DATETIME,
            status TEXT NOT NULL,
            frequency INTEGER NOT NULL,
            counter INTEGER NOT NULL,
            liquidation_distance REAL NOT NULL,
            target_travel_percent REAL NOT NULL,
            liquidation_price REAL NOT NULL,
            notes TEXT,
            position_reference_id TEXT
        )
    """)

    # API_STATUS_COUNTERS
    print("Creating 'api_status_counters' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_status_counters (
            api_name TEXT PRIMARY KEY,
            total_reports INTEGER NOT NULL DEFAULT 0,
            last_updated DATETIME
        )
    """)

    # WALLETS
    print("Creating 'wallets' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            public_address TEXT,
            private_address TEXT,
            image_path TEXT,
            balance REAL DEFAULT 0.0
        )
    """)

    # BROKERS
    print("Creating 'brokers' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brokers (
            name TEXT PRIMARY KEY,
            image_path TEXT NOT NULL,
            web_address TEXT NOT NULL,
            total_holding REAL NOT NULL DEFAULT 0.0
        )
    """)

    # SYSTEM_VARS (with positions/prices timestamps + sources)
    print("Creating 'system_vars' table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_vars (
            id INTEGER PRIMARY KEY,
            last_update_time_positions DATETIME,
            last_update_positions_source TEXT,
            last_update_time_prices DATETIME,
            last_update_prices_source TEXT
        )
    """)

    # Insert row id=1 if not present
    cursor.execute("""
        INSERT OR IGNORE INTO system_vars (
            id,
            last_update_time_positions,
            last_update_positions_source,
            last_update_time_prices,
            last_update_prices_source
        ) VALUES (1, NULL, NULL, NULL, NULL)
    """)

    conn.commit()
    conn.close()
    print("All tables have been dropped & recreated successfully!")

if __name__ == "__main__":
    main()
