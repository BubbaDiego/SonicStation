
# Space Station - 1st Node

import os
import logging
import json
import sqlite3
import asyncio
import pytz
from datetime import datetime
import requests
from flask import Flask
from typing import List
from uuid import uuid4

from flask import (
    Flask,
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    send_file
)

# Panda Stuff
from models import Position
from data_locker import DataLocker
from config_manager import load_config
from config import AppConfig
from calc_services import CalcServices
from price_monitor import PriceMonitor
from alert_manager import AlertManager


app = Flask(__name__)

#@app.route('/')
#def hello_world():
#    return 'Hello from Flask!'

app = Flask(__name__)
app.debug = False  # or True if you want
logger = logging.getLogger("WebAppLogger")
logger.setLevel(logging.DEBUG)
app.secret_key = "i-like-lamp"

# Build absolute paths for DB + config
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "mother_brain.db")
CONFIG_PATH = os.path.join(BASE_DIR, "sonic_config.json")

# Example: If your aggregator needs a mint->asset mapping:
MINT_TO_ASSET = {
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "BTC",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "So11111111111111111111111111111111111111112": "SOL"
}

# Initialize services if you want them once at startup
# (Otherwise, you can do lazy init in routes.)
def initialize_services():
    db_conn = sqlite3.connect(DB_PATH)
    config = load_config_hybrid(CONFIG_PATH, db_conn)
    calc_services = CalcServices()
    manager = AlertManager(
        db_path=DB_PATH,
        poll_interval=60,
        config_path=CONFIG_PATH
    )
    return {
        "db_conn": db_conn,
        "config": config,
        "calc_services": calc_services,
        "manager": manager
    }


manager = AlertManager(
    db_path=DB_PATH,
    poll_interval=60,
    config_path=CONFIG_PATH
)


#######################################
# ROUTES
#######################################

@app.route("/")
def index():
    """Redirect to positions page."""
    return redirect(url_for("positions"))


def get_alert_class(value, low_thresh, med_thresh, high_thresh):
    if high_thresh is None:
        high_thresh = float('inf')
    if med_thresh is None:
        med_thresh = float('inf')
    if low_thresh is None:
        low_thresh = float('-inf')

    if value >= high_thresh:
        return "alert-high"
    elif value >= med_thresh:
        return "alert-medium"
    elif value >= low_thresh:
        return "alert-low"
    else:
        return "alert-high"

@app.route("/positions")
def positions():
    data_locker = DataLocker(db_path=DB_PATH)
    calc_services = CalcServices()

    # 1) Read raw positions from DB
    positions_data = data_locker.read_positions()

    # 2) Fill them with newest price if missing
    positions_data = fill_positions_with_latest_price(positions_data)

    # 3) Enrich each position (PnL, leverage, etc.) via aggregator
    updated_positions = calc_services.aggregator_positions(positions_data, DB_PATH)

    # Attach wallet info
    for pos in updated_positions:
        pos["collateral"] = float(pos.get("collateral") or 0.0)
        wallet_name = pos.get("wallet_name")
        if wallet_name:
            w = data_locker.get_wallet_by_name(wallet_name)
            pos["wallet_name"] = w
        else:
            pos["wallet_name"] = None

    # Load config (alert_ranges)
    config_data = load_app_config()  # or load_config_hybrid(...)
    alert_dict = config_data.alert_ranges or {}

    # Helper to classify a numeric value vs. (low/medium/high)
    def get_alert_class(value, low_thresh, med_thresh, high_thresh):
        if high_thresh is None:
            high_thresh = float('inf')
        if med_thresh is None:
            med_thresh = float('inf')
        if low_thresh is None:
            low_thresh = float('-inf')

        if value >= high_thresh:
            return "alert-high"
        elif value >= med_thresh:
            return "alert-medium"
        elif value >= low_thresh:
            return "alert-low"
        else:
            return ""

    # Example for each sub-range:
    hi_cfg   = alert_dict.get("heat_index_ranges", {})
    hi_low   = hi_cfg.get("low", 0.0)
    hi_med   = hi_cfg.get("medium", 0.0)
    hi_high  = hi_cfg.get("high", None)

    coll_cfg = alert_dict.get("collateral_ranges", {})
    coll_low   = coll_cfg.get("low", 0.0)
    coll_med   = coll_cfg.get("medium", 0.0)
    coll_high  = coll_cfg.get("high", None)

    val_cfg = alert_dict.get("value_ranges", {})
    val_low   = val_cfg.get("low", 0.0)
    val_med   = val_cfg.get("medium", 0.0)
    val_high  = val_cfg.get("high", None)

    size_cfg = alert_dict.get("size_ranges", {})
    size_low   = size_cfg.get("low", 0.0)
    size_med   = size_cfg.get("medium", 0.0)
    size_high  = size_cfg.get("high", None)

    lev_cfg = alert_dict.get("leverage_ranges", {})
    lev_low   = lev_cfg.get("low", 0.0)
    lev_med   = lev_cfg.get("medium", 0.0)
    lev_high  = lev_cfg.get("high", None)

    liqd_cfg = alert_dict.get("liquidation_distance_ranges", {})
    liqd_low   = liqd_cfg.get("low", 0.0)
    liqd_med   = liqd_cfg.get("medium", 0.0)
    liqd_high  = liqd_cfg.get("high", None)

    tliq_cfg = alert_dict.get("travel_percent_liquid_ranges", {})
    tliq_low   = tliq_cfg.get("low", 0.0)
    tliq_med   = tliq_cfg.get("medium", 0.0)
    tliq_high  = tliq_cfg.get("high", None)

    tprof_cfg = alert_dict.get("travel_percent_profit_ranges", {})
    tprof_low   = tprof_cfg.get("low", 0.0)
    tprof_med   = tprof_cfg.get("medium", 0.0)
    tprof_high  = tprof_cfg.get("high", None)

    # Apply alert classes
    for pos in updated_positions:
        # Heat index
        heat_val = float(pos.get("heat_index", 0.0))
        pos["heat_alert_class"] = get_alert_class(heat_val, hi_low, hi_med, hi_high)

        # Collateral
        coll_val = float(pos["collateral"])
        pos["collateral_alert_class"] = get_alert_class(coll_val, coll_low, coll_med, coll_high)

        # Value
        val = float(pos.get("value", 0.0))
        pos["value_alert_class"] = get_alert_class(val, val_low, val_med, val_high)

        # Size
        sz = float(pos.get("size", 0.0))
        pos["size_alert_class"] = get_alert_class(sz, size_low, size_med, size_high)

        # Leverage
        lev = float(pos.get("leverage", 0.0))
        pos["leverage_alert_class"] = get_alert_class(lev, lev_low, lev_med, lev_high)

        # Liquidation Distance
        liqd = float(pos.get("liquidation_distance", 0.0))
        pos["liqdist_alert_class"] = get_alert_class(liqd, liqd_low, liqd_med, liqd_high)

        # Travel Percent (Liquid)
        tliq_val = float(pos.get("current_travel_percent", 0.0))
        pos["travel_liquid_alert_class"] = get_alert_class(tliq_val, tliq_low, tliq_med, tliq_high)

        # Travel Percent (Profit)
        tprof_val = float(pos.get("current_travel_percent", 0.0))
        pos["travel_profit_alert_class"] = get_alert_class(tprof_val, tprof_low, tprof_med, tprof_high)

    # Totals
    totals_dict = calc_services.calculate_totals(updated_positions)

    return render_template("positions.html", positions=updated_positions, totals=totals_dict)


@app.route("/exchanges")
def exchanges():
    """Show the brokers list."""
    data_locker = DataLocker(db_path=DB_PATH)
    brokers_data = data_locker.read_brokers()
    return render_template("exchanges.html", brokers=brokers_data)

@app.route("/edit-position/<position_id>", methods=["POST"])
def edit_position(position_id):
    """Updates size/collateral for a given position."""
    data_locker = DataLocker(db_path=DB_PATH)
    logger.debug(f"Editing position {position_id}.")
    try:
        size = float(request.form.get("size", 0.0))
        collateral = float(request.form.get("collateral", 0.0))
        data_locker.update_position(position_id, size, collateral)
        data_locker.sync_calc_services()  # or sync_dependent_data(), if that’s your method name
        return redirect(url_for("positions"))
    except Exception as e:
        logger.error(f"Error updating position {position_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/delete-position/<position_id>", methods=["POST"])
def delete_position(position_id):
    """Removes one position."""
    data_locker = DataLocker(db_path=DB_PATH)
    logger.debug(f"Deleting position {position_id}")
    try:
        data_locker.cursor.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        data_locker.conn.commit()
        return redirect(url_for("positions"))
    except Exception as e:
        logger.error(f"Error deleting position {position_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/delete-all-positions", methods=["POST"])
def delete_all_positions():
    """Wipes out all rows in `positions` table."""
    data_locker = DataLocker(db_path=DB_PATH)
    logger.debug("Deleting ALL positions")
    try:
        data_locker.delete_all_positions()
        return redirect(url_for("positions"))
    except Exception as e:
        logger.error(f"Error deleting all positions: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/upload-positions", methods=["POST"])
def upload_positions():
    """
    Accepts a file upload with an array of positions (JSON).
    Each pos dict can have "wallet_name" etc. We'll store them in DB.
    """
    data_locker = DataLocker(db_path=DB_PATH)
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400

        file = request.files["file"]
        if not file:
            return jsonify({"error": "Empty file"}), 400

        file_contents = file.read().decode("utf-8").strip()
        if not file_contents:
            return jsonify({"error": "Uploaded file is empty"}), 400

        positions_list = json.loads(file_contents)
        if not isinstance(positions_list, list):
            return jsonify({"error": "Top-level JSON must be a list"}), 400

        for pos_dict in positions_list:
            if "wallet_name" in pos_dict:
                pos_dict["wallet"] = pos_dict["wallet_name"]
            data_locker.create_position(pos_dict)

        return jsonify({"message": "Positions uploaded successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error uploading positions: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/prices", methods=["GET", "POST"])
def prices():
    """
    On GET: Show the 3 top prices (BTC, ETH, SOL) + recent prices + API counters.
    On POST: Insert or update a new manual price.
    """
    data_locker = DataLocker(db_path=DB_PATH)
    if request.method == "POST":
        asset = request.form.get("asset", "BTC")
        raw_price = request.form.get("price", "0.0")
        price_val = float(raw_price)
        data_locker.insert_or_update_price(
            asset_type=asset,
            current_price=price_val,
            source="Manual",
            timestamp=datetime.now()
        )
        return redirect(url_for("prices"))

    top_prices = _get_top_prices_for_assets(DB_PATH, ["BTC", "ETH", "SOL"])
    recent_prices = _get_recent_prices(DB_PATH, limit=15)
    api_counters = data_locker.read_api_counters()

    return render_template(
        "prices.html",
        prices=top_prices,
        recent_prices=recent_prices,
        api_counters=api_counters
    )

@app.route("/update-prices", methods=["POST"])
def update_prices():
    """Calls PriceMonitor.update_prices() to fetch live prices from APIs."""
    pm = PriceMonitor(db_path=DB_PATH, config_path=CONFIG_PATH)
    try:
        asyncio.run(pm.update_prices())
    except Exception as e:
        logger.exception(f"Error updating prices: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok", "message": "Prices updated successfully"})

@app.route("/alert-options", methods=["GET", "POST"])
def alert_options():
    """
    Loads config from 'sonic_config.json' into AppConfig, displays in a form,
    updates & saves it if POST.
    """
    try:
        # 1) Load JSON from disk
        if not os.path.exists(CONFIG_PATH):
            # If no file, create an empty default or raise an error
            return jsonify({"error": "sonic_config.json not found"}), 404

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        config_data = AppConfig(**data)

        if request.method == "POST":
            # 2) Parse form fields, update config_data
            new_low  = float(request.form.get("heat_index_low", 0.0))
            new_med  = float(request.form.get("heat_index_medium", 0.0))
            new_high = float(request.form.get("heat_index_high", 0.0))
            config_data.alert_ranges.heat_index_ranges.low = new_low
            config_data.alert_ranges.heat_index_ranges.medium = new_med
            config_data.alert_ranges.heat_index_ranges.high = new_high

            # Save updated config back to disk
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config_data.model_dump(), f, indent=2)

            flash("Alert settings updated!", "success")
            return redirect(url_for("alert_options"))

        return render_template("alert_options.html", config=config_data)

    except FileNotFoundError:
        return jsonify({"error": "sonic_config.json not found"}), 404
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON in config file"}), 400
    except Exception as e:
        app.logger.error(f"Error in /alert-options: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/system-options", methods=["GET", "POST"])
def system_options():
    """
    Displays system options. On POST, either reset counters or save new config.
    """
    data_locker = DataLocker(db_path=DB_PATH)

    if request.method == "POST":
        config = load_app_config()
        form_action = request.form.get("action")

        if form_action == "reset_counters":
            data_locker.reset_api_counters()
            flash("API report counters have been reset!", "success")
            return redirect(url_for("system_options"))
        else:
            # Update system config
            config.system_config.log_level = request.form.get("log_level", "INFO")
            config.system_config.db_path   = request.form.get("db_path", config.system_config.db_path)

            # For assets:
            assets_str = request.form.get("assets", "")
            config.price_config.assets = [a.strip() for a in assets_str.split(",") if a.strip()]

            # For currency, fetch_timeout...
            config.price_config.currency      = request.form.get("currency", "USD")
            config.price_config.fetch_timeout = int(request.form.get("fetch_timeout", 10))

            # For coingecko/binance enable
            config.api_config.coingecko_api_enabled = request.form.get("coingecko_api_enabled", "ENABLE")
            config.api_config.binance_api_enabled   = request.form.get("binance_api_enabled", "ENABLE")

            # For coinmarketcap key
            config.api_config.coinmarketcap_api_key = request.form.get("coinmarketcap_api_key", "")

            save_app_config(config)

            flash("System options saved!", "success")
            return redirect(url_for("system_options"))

    config = load_app_config()
    return render_template("system_options.html", config=config)

@app.route("/export-config")
def export_config():
    """Send the sonic_config.json file to the user."""
    return send_file(
        CONFIG_PATH,
        as_attachment=True,
        download_name="sonic_config.json",
        mimetype="application/json"
    )

@app.route("/heat", methods=["GET"])
def heat():
    """Show position heat data."""
    data_locker = DataLocker(db_path=DB_PATH)
    calc_services = CalcServices()

    positions_data = data_locker.read_positions()
    positions_data = fill_positions_with_latest_price(positions_data)
    positions_data = calc_services.prepare_positions_for_display(positions_data)

    heat_data = build_heat_data(positions_data)
    return render_template("heat.html", heat_data=heat_data)

@app.route("/alerts")
@app.route("/alerts")
def alerts():
    """
    Displays:
    1) A Market Snapshot (mini_prices) panel (BTC, ETH, SOL) if present in DB
    2) Three info boxes labeled Low/Medium/High based on negative travel_percent in positions
    3) Active Alerts (left), Recent Alerts (right)
    4) "Add New Alert" form at the bottom
    """
    data_locker = DataLocker(DB_PATH)

    # 1) Build the mini_prices list from the 'prices' table
    mini_prices = []
    for asset in ["BTC", "ETH", "SOL"]:
        row = data_locker.get_latest_price(asset)
        if row:
            mini_prices.append({
                "asset_type": row["asset_type"],
                "current_price": float(row["current_price"])
            })
    # If none are found, mini_prices remains empty => "No price data" in the template

    # 2) Calculate Low/Med/High from positions' current_travel_percent
    positions = data_locker.read_positions()

    # Load config to get the travel_percent_liquid_ranges
    config_dict = load_config(CONFIG_PATH, data_locker.get_db_connection())
    liquid_cfg = config_dict["alert_ranges"]["travel_percent_liquid_ranges"]

    low_thresh   = float(liquid_cfg["low"])     # e.g. -25
    med_thresh   = float(liquid_cfg["medium"])  # e.g. -50
    high_thresh  = float(liquid_cfg["high"])    # e.g. -75

    low_alert_count = 0
    medium_alert_count = 0
    high_alert_count = 0

    for pos in positions:
        val = float(pos.get("current_travel_percent", 0.0))
        if val < 0:
            if val <= high_thresh:
                high_alert_count += 1
            elif val <= med_thresh:
                medium_alert_count += 1
            elif val <= low_thresh:
                low_alert_count += 1

    # 3) Fetch real alerts from DB and split into active vs recent
    all_alerts = data_locker.get_alerts()
    active_alerts_data = []
    recent_alerts_data = []
    for alert in all_alerts:
        if alert.get("status", "").lower() == "active":
            active_alerts_data.append(alert)
        else:
            recent_alerts_data.append(alert)

    # 4) Render 'alerts.html' with real data
    return render_template(
        "alerts.html",
        mini_prices=mini_prices,
        low_alert_count=low_alert_count,
        medium_alert_count=medium_alert_count,
        high_alert_count=high_alert_count,
        active_alerts=active_alerts_data,
        recent_alerts=recent_alerts_data
    )



@app.route("/alerts/create", methods=["POST"])
def alerts_create():
    alert_type = request.form.get("alert_type", "")
    asset_type = request.form.get("asset_type", "")
    trigger_value_str = request.form.get("trigger_value", "0")
    condition = request.form.get("condition", "ABOVE")  # <--- read from form

    status = request.form.get("status", "Active")
    notification_type = request.form.get("notification_type", "SMS")
    position_ref = request.form.get("position_reference_id", "")

    try:
        trigger_value = float(trigger_value_str)
    except ValueError:
        trigger_value = 0.0

    new_alert = {
        "id": str(uuid4()),
        "alert_type": alert_type,
        "asset_type": asset_type,
        "trigger_value": trigger_value,
        "condition": condition,           # <--- add it here!
        "notification_type": notification_type,
        "last_triggered": None,
        "status": status,
        "frequency": 0,
        "counter": 0,
        "liquidation_distance": 0.0,
        "target_travel_percent": 0.0,
        "liquidation_price": 0.0,
        "notes": "",
        "position_reference_id": position_ref
    }

    data_locker = DataLocker(DB_PATH)
    data_locker.create_alert(new_alert)  # Must include 'condition' in alert_dict

    flash("New alert created successfully!", "success")
    return redirect(url_for("alerts"))

@app.route("/manual_check_alerts", methods=["POST"])
def manual_check_alerts():
    """
    Called by the "Check Alerts" button to run manager.check_alerts() manually.
    This calls your Travel% logic (and any others you define).
    """
    manager.check_alerts()
    return jsonify({"status": "success", "message": "Alerts have been manually checked!"}), 200

@app.route("/jupiter-perps-proxy", methods=["GET"])
def jupiter_perps_proxy():
    """
    A simple GET route that fetches the positions JSON from Jupiter's Perps API
    and returns it to the client.
    """
    wallet_address = request.args.get("walletAddress", "6vMjsGU63evYuwwGsDHBRnKs1stALR7SYN5V57VZLXca")
    jupiter_url = f"https://perps-api.jup.ag/v1/positions?walletAddress={wallet_address}&showTpslRequests=true"

    try:
        response = requests.get(jupiter_url)
        response.raise_for_status()
        data = response.json()
        return jsonify(data), 200
    except requests.exceptions.HTTPError as http_err:
        app.logger.error(f"HTTP error fetching Jupiter positions: {http_err}")
        return jsonify({"error": f"HTTP {response.status_code}: {response.text}"}), 500
    except Exception as e:
        app.logger.error(f"Error fetching Jupiter positions: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/update-jupiter-positions", methods=["POST"])
def update_jupiter_positions():
    """
    Fetches fresh positions from Jupiter Perps API for all wallets,
    storing them in the DB if they're new.
    """
    data_locker = DataLocker(db_path=DB_PATH)
    try:
        wallets_list = data_locker.read_wallets()
        if not wallets_list:
            app.logger.info("No wallets found in DB.")
            return jsonify({"message": "No wallets found in DB"}), 200

        total_positions_imported = 0
        for w in wallets_list:
            public_addr = w.get("public_address", "").strip()
            if not public_addr:
                app.logger.info(f"Skipping wallet {w['name']} (no public_address).")
                continue

            jupiter_url = (
                "https://perps-api.jup.ag/v1/positions"
                f"?walletAddress={public_addr}&showTpslRequests=true"
            )
            resp = requests.get(jupiter_url)
            resp.raise_for_status()
            data = resp.json()

            data_list = data.get("dataList", [])
            if not data_list:
                app.logger.info(f"No positions for wallet {w['name']} ({public_addr}).")
                continue

            new_positions = []
            for item in data_list:
                try:
                    epoch_time = float(item.get("updatedTime", 0))
                    updated_dt = datetime.fromtimestamp(epoch_time)
                    mint = item.get("marketMint", "")
                    asset_type = MINT_TO_ASSET.get(mint, "BTC")
                    side = item.get("side", "short").capitalize()

                    pos_dict = {
                        "asset_type": asset_type,
                        "position_type": side,
                        "entry_price": float(item.get("entryPrice", 0.0)),
                        "liquidation_price": float(item.get("liquidationPrice", 0.0)),
                        "collateral": float(item.get("collateral", 0.0)),
                        "size": float(item.get("size", 0.0)),
                        "leverage": float(item.get("leverage", 0.0)),
                        "value": float(item.get("value", 0.0)),
                        "last_updated": updated_dt.isoformat(),
                        "wallet_name": w["name"],
                    }
                    new_positions.append(pos_dict)
                except Exception as map_err:
                    app.logger.warning(
                        f"Skipping item for wallet {w['name']} due to mapping error: {map_err}"
                    )

            # Minimal duplicate check
            for p in new_positions:
                duplicate_check = data_locker.cursor.execute(
                    """
                    SELECT COUNT(*)
                      FROM positions
                     WHERE wallet_name = ?
                       AND asset_type = ?
                       AND position_type = ?
                       AND ABS(size - ?) < 0.000001
                       AND ABS(collateral - ?) < 0.000001
                       AND last_updated = ?
                    """,
                    (
                        p["wallet_name"],
                        p["asset_type"],
                        p["position_type"],
                        p["size"],
                        p["collateral"],
                        p["last_updated"],
                    )
                ).fetchone()
                if duplicate_check[0] == 0:
                    data_locker.create_position(p)
                    total_positions_imported += 1
                else:
                    app.logger.info(f"Skipping duplicate Jupiter position {p}")

        return jsonify({
            "message": f"Imported {total_positions_imported} new position(s) from Jupiter."
        }), 200

    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error fetching Jupiter: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/delete-all-jupiter-positions", methods=["POST"])
def delete_all_jupiter_positions():
    """Just an example of how you might clear out data before re-import."""
    data_locker = DataLocker(db_path=DB_PATH)
    data_locker.cursor.execute("DELETE FROM positions WHERE wallet_name IS NOT NULL")
    data_locker.conn.commit()
    return jsonify({"message": "All Jupiter positions deleted."}), 200

@app.route("/delete-alert/<alert_id>", methods=["POST"])
def delete_alert(alert_id):
    data_locker = DataLocker(DB_PATH)
    data_locker.delete_alert(alert_id)
    flash("Alert deleted!", "success")
    return redirect(url_for("alerts"))

@app.route("/update_jupiter", methods=["POST"])
def update_jupiter():
    """
    Example route that calls delete_all_positions(), then update_jupiter_positions(),
    then update_prices().
    """
    delete_all_positions()
    jupiter_resp, jupiter_code = update_jupiter_positions()
    if jupiter_code != 200:
        return jupiter_resp, jupiter_code

    prices_resp = update_prices()
    if prices_resp.status_code != 200:
        return prices_resp

    return jsonify({"message": "Jupiter positions + Prices updated successfully!"}), 200

@app.route("/audio-tester")
def audio_tester():
    return render_template("audio_tester.html")


@app.route("/api/positions_data", methods=["GET"])
def positions_data_api():
    """
    Returns JSON with fresh 'mini_prices', 'positions', and 'totals'.
    Called after /update_jupiter to do a partial (in-browser) update.
    """
    data_locker = DataLocker(db_path=DB_PATH)
    calc_services = CalcServices()

    # 1) mini_prices for BTC, ETH, SOL
    mini_prices = []
    for asset in ["BTC", "ETH", "SOL"]:
        row = data_locker.get_latest_price(asset)
        if row:
            mini_prices.append({
                "asset_type": row["asset_type"],
                "current_price": float(row["current_price"])
            })

    # 2) get your raw positions
    positions_data = data_locker.read_positions()
    # fill in current_price if missing
    positions_data = fill_positions_with_latest_price(positions_data)
    # aggregator logic to compute PnL, classes, etc.
    updated_positions = calc_services.aggregator_positions(positions_data, DB_PATH)

    # Possibly attach wallet info again if needed
    for pos in updated_positions:
        pos["collateral"] = float(pos.get("collateral") or 0.0)
        wallet_name = pos.get("wallet_name")
        if wallet_name:
            w = data_locker.get_wallet_by_name(wallet_name)
            pos["wallet_name"] = w
        else:
            pos["wallet_name"] = None

    # Re-run your alert classification (like in /positions)
    # You can define a local helper or replicate the code. Example:
    config_data = load_app_config()
    alert_dict = config_data.alert_ranges or {}

    def get_alert_class(value, low, med, high):
        if high is None:
            high = float('inf')
        if med is None:
            med = float('inf')
        if low is None:
            low = float('-inf')

        if value >= high:
            return "alert-high"
        elif value >= med:
            return "alert-medium"
        elif value >= low:
            return "alert-low"
        else:
            return ""

    # We'll do at least the heat_index + collateral.
    # If you want the rest (value, size, etc.), replicate the logic similarly:
    hi_cfg   = alert_dict.get("heat_index_ranges", {})
    hi_low   = hi_cfg.get("low", 0.0)
    hi_med   = hi_cfg.get("medium", 0.0)
    hi_high  = hi_cfg.get("high", None)

    coll_cfg = alert_dict.get("collateral_ranges", {})
    coll_low   = coll_cfg.get("low", 0.0)
    coll_med   = coll_cfg.get("medium", 0.0)
    coll_high  = coll_cfg.get("high", None)

    for pos in updated_positions:
        # Heat index
        heat_val = float(pos.get("heat_index", 0.0))
        pos["heat_alert_class"] = get_alert_class(heat_val, hi_low, hi_med, hi_high)

        # Collateral
        coll_val = float(pos.get("collateral", 0.0))
        pos["collateral_alert_class"] = get_alert_class(coll_val, coll_low, coll_med, coll_high)

        # (Do the same for value, size, leverage, etc. if you want all classes updated)

    # 3) compute totals
    totals_dict = calc_services.calculate_totals(updated_positions)

    # 4) Return JSON
    return jsonify({
        "mini_prices": mini_prices,
        "positions": updated_positions,
        "totals": totals_dict
    })

@app.route("/database-viewer")
def database_viewer():
    """Displays all non-system tables + their rows for debugging."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT name 
          FROM sqlite_master
         WHERE type='table'
           AND name NOT LIKE 'sqlite_%'
         ORDER BY name
    """)
    tables = [row["name"] for row in cur.fetchall()]

    db_data = {}
    for table in tables:
        cur.execute(f"PRAGMA table_info({table})")
        columns = [col["name"] for col in cur.fetchall()]
        cur.execute(f"SELECT * FROM {table}")
        rows_raw = cur.fetchall()
        rows = [dict(r) for r in rows_raw]
        db_data[table] = {"columns": columns, "rows": rows}

    conn.close()
    return render_template("database_viewer.html", db_data=db_data)

@app.route("/test-jupiter-perps-proxy")
def test_jupiter_perps_proxy():
    return render_template("test_jupiter_perps.html")

@app.route("/console-test")
def console_test():
    return render_template("console_test.html")

@app.route("/test-jupiter-swap", methods=["GET"])
def test_jupiter_swap():
    return render_template("test_jupiter_swap.html")

# ---------------------------------------
# Utility functions used by routes
# ---------------------------------------

def load_app_config() -> AppConfig:
    """Loads JSON from sonic_config.json into an AppConfig object."""
    if not os.path.exists(CONFIG_PATH):
        return AppConfig()  # or raise an error
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return AppConfig(**data)

def save_app_config(config: AppConfig):
    """Saves the AppConfig back to sonic_config.json."""
    data = config.model_dump()  # For Pydantic v2
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def fill_positions_with_latest_price(positions: List[dict]) -> List[dict]:
    """
    For each position, if 'current_price' is 0 or missing,
    look up the newest price for that asset_type from the DB.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    for pos in positions:
        asset = pos.get("asset_type","BTC").upper()
        if pos.get("current_price", 0.0) > 0:
            continue
        row = cursor.execute("""
            SELECT current_price
              FROM prices
             WHERE asset_type = ?
             ORDER BY last_update_time DESC
             LIMIT 1
        """, (asset,)).fetchone()

        if row:
            pos["current_price"] = float(row["current_price"])
        else:
            pos["current_price"] = 0.0

    conn.close()
    return positions



def _convert_iso_to_pst(iso_str):
    """Converts an ISO datetime string to PST. Returns 'N/A' on failure."""
    if not iso_str or iso_str == "N/A":
        return "N/A"
    pst = pytz.timezone("US/Pacific")
    try:
        dt_obj = datetime.fromisoformat(iso_str)
        dt_pst = dt_obj.astimezone(pst)
        return dt_pst.strftime("%Y-%m-%d %H:%M:%S %Z")
    except:
        return "N/A"

def _get_top_prices_for_assets(db_path, assets=None):
    """For each asset in `assets`, get the newest row from 'prices'."""
    if assets is None:
        assets = ["BTC", "ETH", "SOL"]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    results = []

    for asset in assets:
        row = cur.execute("""
            SELECT asset_type, current_price, last_update_time
              FROM prices
             WHERE asset_type = ?
             ORDER BY last_update_time DESC
             LIMIT 1
        """, (asset,)).fetchone()
        if row:
            iso = row["last_update_time"]
            results.append({
                "asset_type": row["asset_type"],
                "current_price": row["current_price"],
                "last_update_time_pst": _convert_iso_to_pst(iso)
            })
        else:
            results.append({
                "asset_type": asset,
                "current_price": 0.0,
                "last_update_time_pst": "N/A"
            })
    conn.close()
    return results

def _get_recent_prices(db_path, limit=15):
    """Grabs up to `limit` most recent rows from 'prices'."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"""
        SELECT asset_type, current_price, last_update_time
          FROM prices
         ORDER BY last_update_time DESC
         LIMIT {limit}
    """)
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        iso = r["last_update_time"]
        results.append({
            "asset_type": r["asset_type"],
            "current_price": r["current_price"],
            "last_update_time_pst": _convert_iso_to_pst(iso)
        })
    return results

def build_heat_data(positions: List[dict]) -> dict:
    """
    Summarizes positions by asset & side for a 'heat.html' page.
    Example structure with BTC/ETH/SOL -> short/long plus totals.
    """
    structure = {
       "BTC":  {"short": {}, "long": {}},
       "ETH":  {"short": {}, "long": {}},
       "SOL":  {"short": {}, "long": {}},
       "totals": {
         "short": {
           "asset": "Short",
           "collateral": 0.0,
           "value": 0.0,
           "leverage": 0.0,
           "travel_percent": 0.0,
           "heat_index": 0.0,
           "size": 0.0
         },
         "long": {
           "asset": "Long",
           "collateral": 0.0,
           "value": 0.0,
           "leverage": 0.0,
           "travel_percent": 0.0,
           "heat_index": 0.0,
           "size": 0.0
         }
       }
    }

    for pos in positions:
        asset = pos.get("asset_type", "BTC").upper()
        side  = pos.get("position_type", "LONG").lower()

        if asset not in ["BTC", "ETH", "SOL"]:
            continue
        if side not in ["short", "long"]:
            continue

        row = {
          "asset": asset,
          "collateral": float(pos.get("collateral", 0.0)),
          "value": float(pos.get("value", 0.0)),
          "leverage": float(pos.get("leverage", 0.0)),
          "travel_percent": float(pos.get("current_travel_percent", 0.0)),
          "heat_index": float(pos.get("heat_index", 0.0)),
          "size": float(pos.get("size", 0.0))
        }

        structure[asset][side] = row

        totals_side = structure["totals"][side]
        totals_side["collateral"] += row["collateral"]
        totals_side["value"]      += row["value"]
        totals_side["size"]       += row["size"]
        totals_side["travel_percent"] += row["travel_percent"]
        totals_side["heat_index"] += row["heat_index"]
        # If you want average leverage or travel_percent, you'd do a separate calc.

    return structure

# MAIN
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
