import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime
from uuid import uuid4

class DataLocker:
    """
    A synchronous DataLocker that manages database interactions using sqlite3.
    Stores:
      - Multiple rows in the 'prices' table (with 'id' PK, for historical data).
      - 'positions' table,
      - 'alerts' table,
      - 'system_vars' (for timestamps & sources of last updates).
    """

    _instance: Optional['DataLocker'] = None

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.logger = logging.getLogger("DataLockerLogger")
        self.conn = None
        self.cursor = None
        self._initialize_database()

    def _initialize_database(self):
        try:
            self._init_sqlite_if_needed()

            # ... (existing CREATE TABLE statements) ...

            # SYSTEM_VARS table
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_vars (
                    id INTEGER PRIMARY KEY,
                    last_update_time_positions DATETIME,
                    last_update_positions_source TEXT,
                    last_update_time_prices DATETIME,
                    last_update_prices_source TEXT
                )
            """)
            self.cursor.execute("""
                INSERT OR IGNORE INTO system_vars (
                    id,
                    last_update_time_positions,
                    last_update_positions_source,
                    last_update_time_prices,
                    last_update_prices_source
                )
                VALUES (1, NULL, NULL, NULL, NULL)
            """)

            # -- NEW: Check if total_brokerage_balance, total_wallet_balance, total_balance exist
            self.cursor.execute("PRAGMA table_info(system_vars)")
            existing_cols = [row[1] for row in self.cursor.fetchall()]

            # If missing, add them:
            if "total_brokerage_balance" not in existing_cols:
                self.cursor.execute("""
                    ALTER TABLE system_vars
                    ADD COLUMN total_brokerage_balance REAL DEFAULT 0.0
                """)
                self.logger.info("Added 'total_brokerage_balance' column to 'system_vars' table.")

            if "total_wallet_balance" not in existing_cols:
                self.cursor.execute("""
                    ALTER TABLE system_vars
                    ADD COLUMN total_wallet_balance REAL DEFAULT 0.0
                """)
                self.logger.info("Added 'total_wallet_balance' column to 'system_vars' table.")

            if "total_balance" not in existing_cols:
                self.cursor.execute("""
                    ALTER TABLE system_vars
                    ADD COLUMN total_balance REAL DEFAULT 0.0
                """)
                self.logger.info("Added 'total_balance' column to 'system_vars' table.")

            self.conn.commit()
            self.logger.debug("Database initialization complete.")

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
        Returns all rows from api_status_counters, including last_updated.
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
        self._init_sqlite_if_needed()
        self.cursor.execute("UPDATE api_status_counters SET total_reports = 0")
        self.conn.commit()

    def increment_api_report_counter(self, api_name: str) -> None:
        """
        Increments total_reports for api_name by 1, sets last_updated to now.
        """
        self._init_sqlite_if_needed()
        self.cursor.execute(
            "SELECT total_reports FROM api_status_counters WHERE api_name = ?",
            (api_name,)
        )
        row = self.cursor.fetchone()
        now_str = datetime.now().isoformat()
        old_count = row["total_reports"] if row else 0
        self.logger.debug(f"Previous total_reports for {api_name}={old_count}")

        if row is None:
            self.cursor.execute("""
                INSERT INTO api_status_counters (api_name, total_reports, last_updated)
                VALUES (?, 1, ?)
            """, (api_name, now_str))
        else:
            self.cursor.execute("""
                UPDATE api_status_counters
                   SET total_reports = total_reports + 1,
                       last_updated = ?
                 WHERE api_name = ?
            """, (now_str, api_name))

        self.conn.commit()
        self.logger.debug(
            f"Incremented API report counter for {api_name}, last_updated={now_str}."
        )

    def get_balance_vars(self) -> dict:
        """
        Return a dict with the 3 columns:
          "total_brokerage_balance",
          "total_wallet_balance",
          "total_balance"
        from system_vars where id=1
        """
        self._init_sqlite_if_needed()

        # Attempt to fetch the single system_vars row with id=1
        row = self.cursor.execute("""
            SELECT
              total_brokerage_balance,
              total_wallet_balance,
              total_balance
            FROM system_vars
            WHERE id=1
        """).fetchone()

        # If row not found, return default zeros
        if not row:
            return {
                "total_brokerage_balance": 0.0,
                "total_wallet_balance": 0.0,
                "total_balance": 0.0
            }

        # Return them as floats (in case they're None, fallback to 0.0)
        return {
            "total_brokerage_balance": row["total_brokerage_balance"] or 0.0,
            "total_wallet_balance": row["total_wallet_balance"] or 0.0,
            "total_balance": row["total_balance"] or 0.0
        }

    def set_balance_vars(
            self,
            brokerage_balance: float = None,
            wallet_balance: float = None,
            total_balance: float = None
    ):
        """
        Update any of the 3 columns in system_vars (id=1).
        Pass None if you don't want to change that field.
        """
        self._init_sqlite_if_needed()

        # Get existing values so we can preserve anything not being updated
        current = self.get_balance_vars()

        new_brokerage = brokerage_balance if brokerage_balance is not None else current["total_brokerage_balance"]
        new_wallet = wallet_balance if wallet_balance is not None else current["total_wallet_balance"]
        new_total = total_balance if total_balance is not None else current["total_balance"]

        self.cursor.execute("""
            UPDATE system_vars
               SET total_brokerage_balance=?,
                   total_wallet_balance=?,
                   total_balance=?
             WHERE id=1
        """, (new_brokerage, new_wallet, new_total))
        self.conn.commit()

        self.logger.debug(
            f"Updated system_vars => total_brokerage_balance={new_brokerage}, "
            f"total_wallet_balance={new_wallet}, total_balance={new_total}"
        )

    def insert_price(self, price_dict: dict):
        """
        Inserts a new price row.
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
        Returns rows from 'prices' as a list of dicts. Can filter by asset_type.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if asset_type:
                cursor.execute("""
                    SELECT *
                      FROM prices
                     WHERE asset_type=?
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
        cur = conn.cursor()
        cur.execute("SELECT * FROM positions")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def read_prices(self) -> List[dict]:
        """
        Returns all rows from 'prices' as plain dicts.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM prices ORDER BY last_update_time DESC")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_latest_price(self, asset_type: str) -> Optional[dict]:
        """
        Returns the newest price row for this asset_type or None.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT *
                  FROM prices
                 WHERE asset_type=?
                 ORDER BY last_update_time DESC
                 LIMIT 1
            """, (asset_type,))
            row = cur.fetchone()
            conn.close()
            return dict(row) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error in get_latest_price: {e}", exc_info=True)
            return None
        except Exception as ex:
            self.logger.exception(f"Unexpected error in get_latest_price: {ex}")
            return None

    def delete_price(self, price_id: str):
        """
        Delete a price row by ID.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM prices WHERE id=?", (price_id,))
            conn.commit()
            conn.close()
            self.logger.debug(f"Deleted price row ID={price_id}")
        except sqlite3.Error as e:
            self.logger.error(f"Database error in delete_price: {e}", exc_info=True)
            raise
        except Exception as ex:
            self.logger.exception(f"Unexpected error in delete_price: {ex}")
            raise

    # ----------------------------------------------------------------
    # ALERTS
    # ----------------------------------------------------------------

    def create_alert(self, alert_dict: dict):
        """
        Insert a new alert row from a dictionary (with ID, alert_type, etc).
        """
        try:
            if not alert_dict.get("id"):
                alert_dict["id"] = str(uuid4())
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO alerts (
                    id,
                    alert_type,
                    asset_type,
                    trigger_value,
                    condition,
                    notification_type,
                    last_triggered,
                    status,
                    frequency,
                    counter,
                    liquidation_distance,
                    target_travel_percent,
                    liquidation_price,
                    notes,
                    position_reference_id
                ) VALUES (
                    :id, :alert_type, :asset_type,
                    :trigger_value, :condition, :notification_type,
                    :last_triggered, :status, :frequency, :counter,
                    :liquidation_distance, :target_travel_percent,
                    :liquidation_price, :notes, :position_reference_id
                )
            """, alert_dict)
            conn.commit()
            conn.close()
            self.logger.debug(f"Created alert ID={alert_dict['id']}")
        except sqlite3.IntegrityError as ie:
            self.logger.error(f"IntegrityError creating alert: {ie}", exc_info=True)
        except sqlite3.Error as e:
            self.logger.error(f"Database error in create_alert: {e}", exc_info=True)
            raise
        except Exception as ex:
            self.logger.exception(f"Unexpected error in create_alert: {ex}")
            raise

    def get_alerts(self) -> List[dict]:
        """
        Return all alerts as a list of dictionaries.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM alerts")
            rows = cur.fetchall()
            conn.close()
            alert_list = [dict(r) for r in rows]
            self.logger.debug(f"Fetched {len(alert_list)} alerts.")
            return alert_list
        except sqlite3.Error as e:
            self.logger.error(f"Database error in get_alerts: {e}", exc_info=True)
            return []
        except Exception as ex:
            self.logger.exception(f"Unexpected error in get_alerts: {ex}")
            return []

    def update_alert_status(self, alert_id: str, new_status: str):
        """
        Update 'status' field of an alert by ID.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                UPDATE alerts
                   SET status=?
                 WHERE id=?
            """, (new_status, alert_id))
            conn.commit()
            conn.close()
            self.logger.debug(f"Alert {alert_id} => status={new_status}")
        except sqlite3.Error as e:
            self.logger.error(f"DB error update_alert_status: {e}", exc_info=True)
            raise
        except Exception as ex:
            self.logger.exception(f"Error updating alert status: {ex}")
            raise

    def delete_alert(self, alert_id: str):
        """
        Delete alert by ID
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
            conn.commit()
            conn.close()
            self.logger.debug(f"Deleted alert ID={alert_id}")
        except sqlite3.Error as e:
            self.logger.error(f"DB error in delete_alert: {e}", exc_info=True)
            raise
        except Exception as ex:
            self.logger.exception(f"Error deleting alert: {ex}")
            raise

    # ----------------------------------------------------------------
    # Insert/Update Price
    # ----------------------------------------------------------------

    def insert_or_update_price(
            self, asset_type: str, current_price: float,
            source: str, timestamp: Optional[datetime] = None
    ):
        """
        Always insert a new prices row for each update (historical records).
        """
        self._init_sqlite_if_needed()
        if timestamp is None:
            timestamp = datetime.now()

        price_dict = {
            "id": str(uuid4()),
            "asset_type": asset_type,
            "current_price": current_price,
            "previous_price": 0.0,
            "last_update_time": timestamp.isoformat(),
            "previous_update_time": None,
            "source": source
        }

        self.insert_price(price_dict)  # We do a brand-new row every time now!

    # ----------------------------------------------------------------
    # POSITIONS
    # ----------------------------------------------------------------

    def create_position(self, pos_dict: dict):
        """
        Insert new position row. Provides defaults if fields missing.
        """
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
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO positions (
                        id, asset_type, position_type,
                        entry_price, liquidation_price, current_travel_percent,
                        value, collateral, size, wallet_name, leverage, last_updated,
                        alert_reference_id, hedge_buddy_id, current_price,
                        liquidation_distance, heat_index, current_heat_index
                    ) VALUES (
                        :id, :asset_type, :position_type,
                        :entry_price, :liquidation_price, :current_travel_percent,
                        :value, :collateral, :size, :wallet_name, :leverage, :last_updated,
                        :alert_reference_id, :hedge_buddy_id, :current_price,
                        :liquidation_distance, :heat_index, :current_heat_index
                    )
                """, pos_dict)
                conn.commit()
            self.logger.debug(f"Created position ID={pos_dict['id']}")
        except Exception as ex:
            self.logger.exception(f"Error creating position: {ex}")
            raise

    def get_positions(self) -> List[dict]:
        """
        Return all positions as dict.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM positions")
            rows = cur.fetchall()
            conn.close()
            results = [dict(r) for r in rows]
            self.logger.debug(f"Fetched {len(results)} positions.")
            return results
        except sqlite3.Error as e:
            self.logger.error(f"DB error get_positions: {e}", exc_info=True)
            return []
        except Exception as ex:
            self.logger.exception(f"Error get_positions: {ex}")
            return []

    def delete_position(self, position_id: str):
        """
        Delete a single position by ID.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM positions WHERE id=?", (position_id,))
            conn.commit()
            conn.close()
            self.logger.debug(f"Deleted position ID={position_id}")
        except sqlite3.Error as e:
            self.logger.error(f"DB error delete_position: {e}", exc_info=True)
            raise
        except Exception as ex:
            self.logger.exception(f"Error delete_position: {ex}")
            raise

    def delete_all_positions(self):
        """
        Delete all rows from 'positions'
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM positions")
                conn.commit()
            self.logger.debug("Deleted all positions.")
        except Exception as ex:
            self.logger.exception(f"Error in delete_all_positions: {ex}")
            raise

    # ----------------------------------------------------------------
    # GET / SET last update times (system_vars table)
    # with sources included
    # ----------------------------------------------------------------

    def get_last_update_times(self) -> dict:
        """
        Reads the single row from system_vars (id=1) and returns a dict:
        {
          "last_update_time_positions": "...",
          "last_update_positions_source": "...",
          "last_update_time_prices": "...",
          "last_update_prices_source": "..."
        }
        """
        self._init_sqlite_if_needed()
        row = self.cursor.execute("""
            SELECT
                last_update_time_positions,
                last_update_positions_source,
                last_update_time_prices,
                last_update_prices_source
            FROM system_vars
            WHERE id=1
        """).fetchone()

        if not row:
            return {
                "last_update_time_positions": None,
                "last_update_positions_source": None,
                "last_update_time_prices": None,
                "last_update_prices_source": None
            }

        return {
            "last_update_time_positions": row["last_update_time_positions"],
            "last_update_positions_source": row["last_update_positions_source"],
            "last_update_time_prices": row["last_update_time_prices"],
            "last_update_prices_source": row["last_update_prices_source"]
        }

    def set_last_update_times(
        self,
        positions_dt: Optional[datetime] = None,
        positions_source: Optional[str] = None,
        prices_dt: Optional[datetime] = None,
        prices_source: Optional[str] = None
    ):
        """
        Writes the given datetimes + sources into system_vars row (id=1).
        Pass None if you don't want to update that field.
        """
        self._init_sqlite_if_needed()

        # Get existing row
        current = self.get_last_update_times()

        # If caller gave new datetimes or source, use them; else keep old
        new_positions_dt = (
            positions_dt.isoformat()
            if positions_dt
            else current["last_update_time_positions"]
        )
        new_positions_src = (
            positions_source
            if positions_source
            else current["last_update_positions_source"]
        )
        new_prices_dt = (
            prices_dt.isoformat()
            if prices_dt
            else current["last_update_time_prices"]
        )
        new_prices_src = (
            prices_source
            if prices_source
            else current["last_update_prices_source"]
        )

        self.cursor.execute("""
            UPDATE system_vars
               SET last_update_time_positions = ?,
                   last_update_positions_source = ?,
                   last_update_time_prices = ?,
                   last_update_prices_source = ?
             WHERE id=1
        """, (new_positions_dt, new_positions_src, new_prices_dt, new_prices_src))
        self.conn.commit()

        self.logger.debug(
            "Updated system_vars =>"
            f" last_update_time_positions={new_positions_dt},"
            f" last_update_positions_source={new_positions_src},"
            f" last_update_time_prices={new_prices_dt},"
            f" last_update_prices_source={new_prices_src}"
        )

    # ----------------------------------------------------------------
    # Wallet & Broker
    # ----------------------------------------------------------------

    def read_wallets(self) -> List[dict]:
        self._init_sqlite_if_needed()
        self.cursor.execute("SELECT * FROM wallets")
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
        self._init_sqlite_if_needed()
        self.logger.info(f"Deleting positions for wallet: {wallet_name}")
        self.cursor.execute("DELETE FROM positions WHERE wallet_name=?", (wallet_name,))
        self.conn.commit()

    def update_position(self, position_id: str, size: float, collateral: float):
        """
        Updates the size & collateral of a position by ID
        """
        try:
            query = """
            UPDATE positions
               SET size=?,
                   collateral=?
             WHERE id=?
            """
            self._init_sqlite_if_needed()
            self.cursor.execute(query, (size, collateral, position_id))
            self.conn.commit()
        except Exception as ex:
            print(f"Error updating position {position_id}: {ex}")
            raise

    def create_wallet(self, wallet_dict: dict):
        """
        Insert new wallet row from dict.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO wallets (name, public_address, private_address, image_path, balance)
                VALUES (?,?,?,?,?)
            """, (
                wallet_dict.get("name"),
                wallet_dict.get("public_address"),
                wallet_dict.get("private_address"),
                wallet_dict.get("image_path"),
                wallet_dict.get("balance", 0.0)
            ))
            conn.commit()
            conn.close()
        except Exception as ex:
            self.logger.exception(f"Error creating wallet: {ex}")
            raise

    def create_broker(self, broker_dict: dict):
        """
        Insert or replace broker row from dict.
        """
        self._init_sqlite_if_needed()
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO brokers (name, image_path, web_address, total_holding)
                VALUES (?,?,?,?)
            """, (
                broker_dict.get("name"),
                broker_dict.get("image_path"),
                broker_dict.get("web_address"),
                broker_dict.get("total_holding", 0.0)
            ))
            self.conn.commit()
        except sqlite3.Error as ex:
            self.logger.error(f"DB error create_broker: {ex}", exc_info=True)
            raise

    def read_brokers(self) -> List[dict]:
        """
        Returns brokers as a list of dicts
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
        Return positions as raw dict from SELECT *.
        """
        self._init_sqlite_if_needed()
        results: List[Dict] = []
        try:
            self.logger.debug("Reading positions raw...")
            self.cursor.execute("SELECT * FROM positions")
            rows = self.cursor.fetchall()
            for row in rows:
                results.append(dict(row))
            self.logger.debug(f"Fetched {len(results)} raw positions.")
            return results
        except Exception as ex:
            self.logger.error(f"Error reading raw positions: {ex}", exc_info=True)
            return []

    def update_position_size(self, position_id: str, new_size: float):
        """
        Update only the 'size' field of a position
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                UPDATE positions
                   SET size=?
                 WHERE id=?
            """, (new_size, position_id))
            conn.commit()
            conn.close()
            self.logger.debug(f"Updated position {position_id} => size={new_size}")
        except sqlite3.Error as ex:
            self.logger.error(f"DB error in update_position_size: {ex}", exc_info=True)
            raise
        except Exception as ex:
            self.logger.exception(f"Error update_position_size: {ex}")
            raise

    def read_wallets(self) -> List[dict]:
        self._init_sqlite_if_needed()
        self.cursor.execute("SELECT * FROM wallets")
        rows = self.cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                "name": r["name"],
                "public_address": r["public_address"],
                "private_address": r["private_address"],
                "image_path": r["image_path"],
                "balance": float(r["balance"])
            })
        return results

    def read_brokers(self) -> List[dict]:
        self._init_sqlite_if_needed()
        self.cursor.execute("SELECT * FROM brokers")
        rows = self.cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                "name": r["name"],
                "image_path": r["image_path"],
                "web_address": r["web_address"],
                "total_holding": float(r["total_holding"])
            })
        return results

    def get_balance_vars(self) -> dict:
        """
        Return a dict with the 3 columns:
          "total_brokerage_balance",
          "total_wallet_balance",
          "total_balance"
        from system_vars where id=1
        """
        self._init_sqlite_if_needed()

        row = self.cursor.execute("""
            SELECT
              total_brokerage_balance,
              total_wallet_balance,
              total_balance
            FROM system_vars
            WHERE id=1
        """).fetchone()

        if not row:
            return {
                "total_brokerage_balance": 0.0,
                "total_wallet_balance": 0.0,
                "total_balance": 0.0
            }
        return {
            "total_brokerage_balance": row["total_brokerage_balance"] or 0.0,
            "total_wallet_balance": row["total_wallet_balance"] or 0.0,
            "total_balance": row["total_balance"] or 0.0
        }

    def set_balance_vars(
            self,
            brokerage_balance: float = None,
            wallet_balance: float = None,
            total_balance: float = None
    ):
        """
        Update any of the 3 columns in system_vars. Pass None if you don't want to change that value.
        """
        self._init_sqlite_if_needed()

        # Read old values
        current = self.get_balance_vars()
        new_brokerage = brokerage_balance if brokerage_balance is not None else current["total_brokerage_balance"]
        new_wallet = wallet_balance if wallet_balance is not None else current["total_wallet_balance"]
        new_total = total_balance if total_balance is not None else current["total_balance"]

        self.cursor.execute("""
            UPDATE system_vars
               SET total_brokerage_balance=?,
                   total_wallet_balance=?,
                   total_balance=?
             WHERE id=1
        """, (new_brokerage, new_wallet, new_total))
        self.conn.commit()

        self.logger.debug(
            f"Updated system_vars => total_brokerage_balance={new_brokerage}, "
            f"total_wallet_balance={new_wallet}, total_balance={new_total}"
        )


    def get_wallet_by_name(self, wallet_name: str) -> Optional[dict]:
        """
        Returns a single wallet row (dict) by name or None.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute("""
            SELECT name,
                   public_address,
                   private_address,
                   image_path,
                   balance
              FROM wallets
             WHERE name=?
             LIMIT 1
        """, (wallet_name,)).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "name": row["name"],
            "public_address": row["public_address"],
            "private_address": row["private_address"],
            "image_path": row["image_path"],
            "balance": row["balance"]
        }

    def close(self):
        """
        Closes DB connection if opened.
        """
        if self.conn:
            self.conn.close()
            self.logger.debug("Database connection closed.")
