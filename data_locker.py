import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime
from uuid import uuid4

# Removed references like:
#   from data.models import Price, Alert, Position, AssetType, Status, CryptoWallet, Broker
#   from calc_services import CalcServices
#   from pydantic import ValidationError

class DataLocker:
    """
    A synchronous DataLocker that manages database interactions using sqlite3.
    Stores:
      - Multiple rows in the 'prices' table (with 'id' PK, for historical data).
      - 'positions' table,
      - 'alerts' table.
    """

    _instance: Optional['DataLocker'] = None

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.logger = logging.getLogger("DataLockerLogger")
        self.conn = None
        self.cursor = None
        self._initialize_database()

    def _initialize_database(self):
        """
        Initializes the database by creating necessary tables if they do not exist.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # PRICES TABLE
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

            # POSITIONS TABLE
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
                    current_heat_index REAL NOT NULL DEFAULT 0.0
                )
            """)

            # (2) If 'wallet_name' column is missing, add it.
            cursor.execute("PRAGMA table_info(positions)")
            columns = [row[1] for row in cursor.fetchall()]
            if "wallet_name" not in columns:
                cursor.execute("""
                    ALTER TABLE positions
                    ADD COLUMN wallet_name TEXT NOT NULL DEFAULT 'Default'
                """)
                print("Added 'wallet_name' column to 'positions' table.")

            # ALERTS TABLE
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY,
                    alert_type TEXT NOT NULL,
                    trigger_value REAL NOT NULL,
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

            # NEW TABLE: API STATUS COUNTERS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_status_counters (
                    api_name TEXT PRIMARY KEY,
                    total_reports INTEGER NOT NULL DEFAULT 0,
                    last_updated DATETIME
                )
            """)

            # WALLET STUFF
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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS brokers (
                    name TEXT PRIMARY KEY,
                    image_path TEXT NOT NULL,
                    web_address TEXT NOT NULL,
                    total_holding REAL NOT NULL DEFAULT 0.0
                )
            """)

            conn.commit()
            conn.close()

        except sqlite3.Error as e:
            self.logger.error(f"Error initializing database: {e}", exc_info=True)
            raise

    @classmethod
    def get_instance(cls, db_path: str) -> 'DataLocker':
        """
        Returns a singleton-ish instance of DataLocker.
        """
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    def _init_sqlite_if_needed(self):
        """
        Ensures self.conn and self.cursor are available.
        """
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row

        if self.cursor is None:
            self.cursor = self.conn.cursor()

    def get_db_connection(self) -> sqlite3.Connection:
        """
        Returns the underlying sqlite3 Connection, ensuring it's initialized first.
        """
        self._init_sqlite_if_needed()
        return self.conn

    # ----------------------------------------------------------------
    # PRICES
    # ----------------------------------------------------------------

    def read_api_counters(self) -> List[dict]:
        """
        Returns all rows from `api_status_counters`, including last_updated.
        """
        self._init_sqlite_if_needed()
        self.cursor.execute("""
            SELECT api_name, total_reports, last_updated
              FROM api_status_counters
             ORDER BY api_name
        """)
        rows = self.cursor.fetchall()

        results = []
        for r in rows:
            results.append({
                "api_name": r["api_name"],
                "total_reports": r["total_reports"],
                "last_updated": r["last_updated"]
            })
        return results

    def reset_api_counters(self):
        """
        Sets total_reports=0 for every row in api_status_counters.
        """
        self._init_sqlite_if_needed()
        self.cursor.execute("UPDATE api_status_counters SET total_reports = 0")
        self.conn.commit()

    def increment_api_report_counter(self, api_name: str) -> None:
        """
        Increments total_reports for the specified api_name by 1.
        Also sets last_updated to the current time.
        """
        self._init_sqlite_if_needed()

        self.cursor.execute(
            "SELECT total_reports FROM api_status_counters WHERE api_name = ?",
            (api_name,)
        )
        row = self.cursor.fetchone()

        now_str = datetime.now().isoformat()
        old_count = row["total_reports"] if row else 0
        self.logger.debug(f"Previous total_reports for {api_name} = {old_count}")

        if row is None:
            # Insert new row
            self.cursor.execute("""
                INSERT INTO api_status_counters (api_name, total_reports, last_updated)
                VALUES (?, 1, ?)
            """, (api_name, now_str))
        else:
            # Increment existing
            self.cursor.execute("""
                UPDATE api_status_counters
                   SET total_reports = total_reports + 1,
                       last_updated = ?
                 WHERE api_name = ?
            """, (now_str, api_name))

        self.conn.commit()
        self.logger.debug(f"Incremented API report counter for {api_name}, set last_updated={now_str}.")

    def insert_price(self, price_dict: dict):
        """
        Inserts a new price row (basic dictionary-based approach).
        """
        try:
            if "id" not in price_dict:
                price_dict["id"] = str(uuid4())

            if "asset_type" not in price_dict:
                price_dict["asset_type"] = "BTC"
            if "current_price" not in price_dict:
                price_dict["current_price"] = 1.0
            if "previous_price" not in price_dict:
                price_dict["previous_price"] = 0.0
            if "last_update_time" not in price_dict:
                price_dict["last_update_time"] = datetime.now().isoformat()
            if "previous_update_time" not in price_dict:
                price_dict["previous_update_time"] = None
            if "source" not in price_dict:
                price_dict["source"] = "Manual"

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO prices (
                    id,
                    asset_type,
                    current_price,
                    previous_price,
                    last_update_time,
                    previous_update_time,
                    source
                )
                VALUES (
                    :id, :asset_type, :current_price, :previous_price,
                    :last_update_time, :previous_update_time, :source
                )
            """, price_dict)
            conn.commit()
            conn.close()

            self.logger.debug(f"Inserted price row with ID={price_dict['id']}")
        except Exception as e:
            self.logger.exception(f"Unexpected error in insert_price: {e}")
            raise

    def get_prices(self, asset_type: Optional[str] = None) -> List[dict]:
        """
        Retrieves rows from 'prices' table as a list of dictionaries.
        If asset_type is provided, filters by that asset.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if asset_type:
                cursor.execute("""
                    SELECT *
                      FROM prices
                     WHERE asset_type = ?
                     ORDER BY last_update_time DESC
                """, (asset_type,))
            else:
                cursor.execute("""
                    SELECT *
                      FROM prices
                     ORDER BY last_update_time DESC
                """)

            rows = cursor.fetchall()
            conn.close()

            price_list = [dict(row) for row in rows]
            self.logger.debug(f"Retrieved {len(price_list)} price rows.")
            return price_list

        except sqlite3.Error as e:
            self.logger.error(f"Database error in get_prices: {e}", exc_info=True)
            return []
        except Exception as e:
            self.logger.exception(f"Unexpected error in get_prices: {e}")
            return []

    def read_positions(self) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions")
        rows = cursor.fetchall()
        conn.close()

        return [dict(r) for r in rows]

    def read_prices(self) -> List[dict]:
        """
        Returns all rows from `prices` as a list of plain dictionaries.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM prices ORDER BY last_update_time DESC")
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_latest_price(self, asset_type: str) -> Optional[dict]:
        """
        Return the single most recent price row for the given asset, or None.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT *
                  FROM prices
                 WHERE asset_type = ?
                 ORDER BY last_update_time DESC
                 LIMIT 1
            """, (asset_type,))
            row = cursor.fetchone()
            conn.close()

            return dict(row) if row else None

        except sqlite3.Error as e:
            self.logger.error(f"Database error in get_latest_price: {e}", exc_info=True)
            return None
        except Exception as e:
            self.logger.exception(f"Unexpected error in get_latest_price: {e}")
            return None

    def delete_price(self, price_id: str):
        """ Delete a price row by its 'id'. """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM prices WHERE id = ?", (price_id,))
            conn.commit()
            conn.close()
            self.logger.debug(f"Deleted price row with ID={price_id}")
        except sqlite3.Error as e:
            self.logger.error(f"Database error in delete_price: {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error in delete_price: {e}")
            raise

    # ----------------------------------------------------------------
    # ALERTS CRUD (using dict instead of a local Alert class)
    # ----------------------------------------------------------------

    def create_alert(self, alert_dict: dict):
        """
        Inserts a new alert record from a dictionary.
        """
        try:
            if not alert_dict.get("id"):
                alert_dict["id"] = str(uuid4())

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alerts (
                    id, alert_type, trigger_value, notification_type, last_triggered,
                    status, frequency, counter, liquidation_distance, target_travel_percent,
                    liquidation_price, notes, position_reference_id
                )
                VALUES (
                    :id, :alert_type, :trigger_value, :notification_type, :last_triggered,
                    :status, :frequency, :counter, :liquidation_distance, :target_travel_percent,
                    :liquidation_price, :notes, :position_reference_id
                )
            """, alert_dict)
            conn.commit()
            conn.close()
            self.logger.debug(f"Created alert with ID={alert_dict['id']}")

        except sqlite3.IntegrityError as ie:
            self.logger.error(f"IntegrityError during alert creation: {ie}", exc_info=True)
        except sqlite3.Error as e:
            self.logger.error(f"Database error in create_alert: {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error in create_alert: {e}")
            raise

    def get_alerts(self) -> List[dict]:
        """ Fetch all alerts as a list of dictionaries. """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM alerts")
            rows = cursor.fetchall()
            conn.close()

            alert_list = [dict(row) for row in rows]
            self.logger.debug(f"Retrieved {len(alert_list)} alerts from DB.")
            return alert_list

        except sqlite3.Error as e:
            self.logger.error(f"Database error in get_alerts: {e}", exc_info=True)
            return []
        except Exception as e:
            self.logger.exception(f"Unexpected error in get_alerts: {e}")
            return []

    def update_alert_status(self, alert_id: str, new_status: str):
        """ Update the 'status' of an alert by ID. (expects a string) """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE alerts
                   SET status = ?
                 WHERE id = ?
            """, (new_status, alert_id))
            conn.commit()
            conn.close()
            self.logger.debug(f"Updated alert {alert_id} status to {new_status}")
        except sqlite3.Error as e:
            self.logger.error(f"Database error in update_alert_status: {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error in update_alert_status: {e}")
            raise

    def delete_alert(self, alert_id: str):
        """ Delete an alert by ID. """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
            conn.close()
            self.logger.debug(f"Deleted alert ID={alert_id}")
        except sqlite3.Error as e:
            self.logger.error(f"Database error in delete_alert: {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error in delete_alert: {e}")
            raise

    # ----------------------------------------------------------------
    # Insert/Update Price
    # ----------------------------------------------------------------
    def insert_or_update_price(self, asset_type: str, current_price: float, source: str, timestamp=None):
        """
        Updates or inserts a price row, identified by asset_type.
        """
        self._init_sqlite_if_needed()

        if timestamp is None:
            timestamp = datetime.now()

        # Check if there's an existing row for this asset
        self.cursor.execute("SELECT id FROM prices WHERE asset_type = ?", (asset_type,))
        row = self.cursor.fetchone()

        if row:
            # existing row => UPDATE
            try:
                self.logger.debug(f"Updating existing price row for {asset_type}.")
                self.cursor.execute("""
                    UPDATE prices
                       SET current_price = ?,
                           last_update_time = ?,
                           source = ?
                     WHERE asset_type = ?
                """, (current_price, timestamp.isoformat(), source, asset_type))
                self.conn.commit()
            except Exception as e:
                self.logger.error(f"Error updating existing price row for {asset_type}: {e}", exc_info=True)
        else:
            # no row => Insert new
            self.logger.debug(f"No existing row for {asset_type}; inserting new price row.")
            price_dict = {
                "id": str(uuid4()),
                "asset_type": asset_type,
                "current_price": current_price,
                "previous_price": 0.0,
                "last_update_time": timestamp.isoformat(),
                "previous_update_time": None,
                "source": source
            }
            self.insert_price(price_dict)

    # ----------------------------------------------------------------
    # POSITIONS
    # ----------------------------------------------------------------

    def create_position(self, pos_dict: dict):
        # Provide defaults for missing fields
        if "id" not in pos_dict:
            pos_dict["id"] = str(uuid4())
        pos_dict.setdefault("asset_type", "BTC")
        pos_dict.setdefault("position_type", "LONG")
        pos_dict.setdefault("entry_price", 0.0)
        pos_dict.setdefault("liquidation_price", 0.0)
        pos_dict.setdefault("current_travel_percent", 0.0)
        pos_dict.setdefault("value", 0.0)
        pos_dict.setdefault("collateral", 0.0)
        pos_dict.setdefault("size", 0.0)
        pos_dict.setdefault("leverage", 0.0)
        pos_dict.setdefault("wallet_name", "Default")
        pos_dict.setdefault("last_updated", datetime.now().isoformat())
        pos_dict.setdefault("alert_reference_id", None)
        pos_dict.setdefault("hedge_buddy_id", None)
        pos_dict.setdefault("current_price", 0.0)
        pos_dict.setdefault("liquidation_distance", None)
        pos_dict.setdefault("heat_index", 0.0)
        pos_dict.setdefault("current_heat_index", 0.0)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO positions (
                        id, asset_type, position_type,
                        entry_price, liquidation_price, current_travel_percent,
                        value, collateral, size, wallet_name, leverage, last_updated,
                        alert_reference_id, hedge_buddy_id, current_price,
                        liquidation_distance, heat_index, current_heat_index
                    )
                    VALUES (
                        :id, :asset_type, :position_type,
                        :entry_price, :liquidation_price, :current_travel_percent,
                        :value, :collateral, :size, :wallet_name, :leverage, :last_updated,
                        :alert_reference_id, :hedge_buddy_id, :current_price,
                        :liquidation_distance, :heat_index, :current_heat_index
                    )
                """, pos_dict)
                conn.commit()

            self.logger.debug(f"Created position with ID={pos_dict['id']}")
        except Exception as e:
            self.logger.exception(f"Unexpected error in create_position: {e}")
            raise

    def get_positions(self) -> List[dict]:
        """
        Returns all positions as a list of plain dictionaries.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            rows = cursor.fetchall()
            conn.close()

            results = [dict(row) for row in rows]
            self.logger.debug(f"Retrieved {len(results)} positions (dicts).")
            return results

        except sqlite3.Error as e:
            self.logger.error(f"Database error in get_positions: {e}", exc_info=True)
            return []
        except Exception as e:
            self.logger.exception(f"Unexpected error in get_positions: {e}")
            return []

    def delete_position(self, position_id: str):
        """ Delete a position by ID. """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions WHERE id = ?", (position_id,))
            conn.commit()
            conn.close()
            self.logger.debug(f"Deleted position with ID={position_id}")
        except sqlite3.Error as e:
            self.logger.error(f"Database error in delete_position: {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error in delete_position: {e}")
            raise

    def read_wallets(self) -> List[dict]:
        """
        Returns all rows from `wallets` as a list of dicts.
        """
        self._init_sqlite_if_needed()
        self.cursor.execute("""SELECT * FROM wallets""")
        rows = self.cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                "name": row["name"],
                "public_address": row["public_address"],
                "private_address": row["private_address"],
                "image_path": row["image_path"],
                "balance": row["balance"]
            })
        return results

    def delete_positions_for_wallet(self, wallet_name: str):
        """
        Removes all rows in 'positions' for the specified wallet_name.
        """
        self._init_sqlite_if_needed()
        self.logger.info(f"Deleting old positions for wallet: {wallet_name}")

        self.cursor.execute("DELETE FROM positions WHERE wallet_name = ?", (wallet_name,))
        self.conn.commit()

    def update_position(self, position_id: str, size: float, collateral: float):
        """
        Updates the given position in the database with new size and collateral.
        """
        try:
            query = """
            UPDATE positions
               SET size = ?,
                   collateral = ?
             WHERE id = ?
            """
            self.cursor.execute(query, (size, collateral, position_id))
            self.conn.commit()
        except Exception as e:
            print(f"Error updating position {position_id}: {e}")
            raise

    def delete_all_positions(self):
        """
        Deletes ALL rows in the 'positions' table.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM positions")
                conn.commit()

            self.logger.debug("Deleted all positions.")
        except Exception as e:
            self.logger.exception(f"Error deleting all positions: {e}")
            raise

    def create_wallet(self, wallet_dict: dict):
        """
        Creates a new wallet row from a dictionary with keys:
        name, public_address, private_address, image_path, balance
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO wallets (name, public_address, private_address, image_path, balance)
                VALUES (?, ?, ?, ?, ?)
            """, (
                wallet_dict.get("name"),
                wallet_dict.get("public_address"),
                wallet_dict.get("private_address"),
                wallet_dict.get("image_path"),
                wallet_dict.get("balance", 0.0)
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.exception(f"Error creating wallet: {e}")
            raise

    def create_broker(self, broker_dict: dict):
        """
        Creates or replaces a broker row, from a dictionary with keys:
        name, image_path, web_address, total_holding
        """
        self._init_sqlite_if_needed()
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO brokers (name, image_path, web_address, total_holding)
                VALUES (?, ?, ?, ?)
            """, (
                broker_dict.get("name"),
                broker_dict.get("image_path"),
                broker_dict.get("web_address"),
                broker_dict.get("total_holding", 0.0)
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"Database error in create_broker: {e}", exc_info=True)
            raise

    def read_brokers(self) -> List[dict]:
        """
        Returns a list of brokers (dict format).
        """
        self._init_sqlite_if_needed()
        self.cursor.execute("SELECT * FROM brokers")
        rows = self.cursor.fetchall()
        results = []
        for row in rows:
            results.append({
                "name": row["name"],
                "image_path": row["image_path"],
                "web_address": row["web_address"],
                "total_holding": row["total_holding"]
            })
        return results

    def read_positions_raw(self) -> List[Dict]:
        """
        Fetches positions as raw dictionaries directly from SQLite.
        """
        self._init_sqlite_if_needed()
        results: List[Dict] = []
        try:
            self.logger.debug("Fetching positions as raw dictionaries...")
            self.cursor.execute("SELECT * FROM positions")
            rows = self.cursor.fetchall()

            for row in rows:
                results.append(dict(row))

            self.logger.debug(f"Fetched {len(results)} positions (raw dict).")
            return results

        except Exception as e:
            self.logger.error(f"Error fetching raw dict positions: {e}", exc_info=True)
            return []

    def update_position_size(self, position_id: str, new_size: float):
        """ Update the 'size' field for a given position. """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE positions
                   SET size = ?
                 WHERE id = ?
            """, (new_size, position_id))
            conn.commit()
            conn.close()
            self.logger.debug(f"Updated size of position {position_id} to {new_size}.")
        except sqlite3.Error as e:
            self.logger.error(f"Database error in update_position_size: {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.exception(f"Unexpected error in update_position_size: {e}")
            raise

    # Removed the “positions()” function that referenced external frameworks/functions:
    #   - fill_positions_with_latest_price
    #   - calc_services
    #   - render_template
    #   - DB_PATH
    # etc.

    def get_wallet_by_name(self, wallet_name: str) -> Optional[dict]:
        """
        Return a dict with the wallet's info, including image_path.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        row = cursor.execute("""
            SELECT name,
                   public_address,
                   private_address,
                   image_path,
                   balance
              FROM wallets
             WHERE name = ?
             LIMIT 1
        """, (wallet_name,)).fetchone()

        conn.close()
        if row is None:
            return None

        return {
            "name": row["name"],
            "public_address": row["public_address"],
            "private_address": row["private_address"],
            "image_path": row["image_path"],
            "balance": row["balance"]
        }

    # Removed the duplicate `_delete_positions_for_wallet` function
    # that referenced `app.logger` and a global DB_PATH.


    def close(self):
        if self.conn:
            self.conn.close()
