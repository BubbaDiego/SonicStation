import sqlite3
import os

# Adjust as needed for your environment
DB_PATH = "mother_brain.db"

def reset_alerts_table(db_path: str):
    """
    Drops the 'alerts' table if it exists, then recreates it
    with all necessary columns, including 'asset_type' and 'condition'.
    """
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1) Drop table if exists
    cursor.execute("DROP TABLE IF EXISTS alerts")
    print("Dropped existing 'alerts' table.")

    # 2) Recreate with 'condition' and 'asset_type'
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT NOT NULL,
            asset_type TEXT,            -- e.g. BTC, ETH, or blank
            trigger_value REAL NOT NULL,
            condition TEXT NOT NULL DEFAULT 'ABOVE',   -- 'ABOVE' or 'BELOW'
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
    print("Recreated 'alerts' table with 'asset_type', 'condition', etc.")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    reset_alerts_table(DB_PATH)
    print("Done! The 'alerts' table was dropped & recreated successfully.")
