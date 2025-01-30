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
app.debug = False
logger = logging.getLogger("WebAppLogger")
logger.setLevel(logging.DEBUG)
app.secret_key = "i-like-lamp"

# Build absolute paths
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "mother_brain.db")
CONFIG_PATH = os.path.join(BASE_DIR, "sonic_config.json")

MINT_TO_ASSET = {
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "BTC",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "So11111111111111111111111111111111111111112": "SOL"
}

manager = AlertManager(
    db_path=DB_PATH,
    poll_interval=60,
    config_path=CONFIG_PATH
)

##################################################
# ROUTES
##################################################

@app.route("/")
def index():
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
    data_locker = DataLocker(DB_PATH)
    calc_services = CalcServices()

    # 0) Gather mini_prices for BTC, ETH, SOL
    mini_prices = []
    for asset in ["BTC", "ETH", "SOL"]:
        row = data_locker.get_latest_price(asset)
        if row:
            mini_prices.append({
                "asset_type": row["asset_type"],
                "current_price": float(row["current_price"])
            })

    # 1) raw from DB
    positions_data = data_locker.read_positions()
    # 2) fill missing price
    positions_data = fill_positions_with_latest_price(positions_data)
    # 3) aggregator => updated positions
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

    config_data = load_app_config()
    alert_dict = config_data.alert_ranges or {}

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

    for pos in updated_positions:
        heat_val = float(pos.get("heat_index", 0.0))
        pos["heat_alert_class"] = get_alert_class(heat_val, hi_low, hi_med, hi_high)

        coll_val = float(pos["collateral"])
        pos["collateral_alert_class"] = get_alert_class(coll_val, coll_low, coll_med, coll_high)

        val = float(pos.get("value", 0.0))
        pos["value_alert_class"] = get_alert_class(val, val_low, val_med, val_high)

        sz = float(pos.get("size", 0.0))
        pos["size_alert_class"] = get_alert_class(sz, size_low, size_med, size_high)

        lev = float(pos.get("leverage", 0.0))
        pos["leverage_alert_class"] = get_alert_class(lev, lev_low, lev_med, lev_high)

        liqd = float(pos.get("liquidation_distance", 0.0))
        pos["liqdist_alert_class"] = get_alert_class(liqd, liqd_low, liqd_med, liqd_high)

        tliq_val = float(pos.get("current_travel_percent", 0.0))
        pos["travel_liquid_alert_class"] = get_alert_class(tliq_val, tliq_low, tliq_med, tliq_high)

        tprof_val = float(pos.get("current_travel_percent", 0.0))
        pos["travel_profit_alert_class"] = get_alert_class(tprof_val, tprof_low, tprof_med, tprof_high)

    totals_dict = calc_services.calculate_totals(updated_positions)

    return render_template(
        "positions.html",
        positions=updated_positions,
        totals=totals_dict,
        mini_prices=mini_prices  # <--- ADDED THIS
    )

@app.route("/exchanges")
def exchanges():
    data_locker = DataLocker(DB_PATH)
    brokers_data = data_locker.read_brokers()
    return render_template("exchanges.html", brokers=brokers_data)


@app.route("/edit-position/<position_id>", methods=["POST"])
def edit_position(position_id):
    data_locker = DataLocker(DB_PATH)
    logger.debug(f"Editing position {position_id}.")
    try:
        size = float(request.form.get("size", 0.0))
        collateral = float(request.form.get("collateral", 0.0))
        data_locker.update_position(position_id, size, collateral)
        data_locker.sync_calc_services()
        return redirect(url_for("positions"))
    except Exception as e:
        logger.error(f"Error updating position {position_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/delete-position/<position_id>", methods=["POST"])
def delete_position(position_id):
    data_locker = DataLocker(DB_PATH)
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
    data_locker = DataLocker(DB_PATH)
    logger.debug("Deleting ALL positions")
    try:
        data_locker.delete_all_positions()
        return redirect(url_for("positions"))
    except Exception as e:
        logger.error(f"Error deleting all positions: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/delete-all-prices", methods=["POST"])
def delete_all_prices():
    data_locker = DataLocker(DB_PATH)
    data_locker.cursor.execute("DELETE FROM prices")
    data_locker.conn.commit()
    return redirect(url_for("database_viewer"))

@app.route("/upload-positions", methods=["POST"])
def upload_positions():
    data_locker = DataLocker(DB_PATH)
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
    data_locker = DataLocker(DB_PATH)
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
    """
    Calls PriceMonitor.update_prices() to fetch live prices from APIs.
    After success, sets last_update_time_prices & last_update_prices_source = "API"
    (or user) if you pass ?source=user, for example.
    """
    source = request.args.get("source") or request.form.get("source") or "API"

    pm = PriceMonitor(db_path=DB_PATH, config_path=CONFIG_PATH)
    try:
        asyncio.run(pm.update_prices())

        # We updated prices => store the last_update_time_prices + source
        data_locker = DataLocker(db_path=DB_PATH)
        now = datetime.now()
        data_locker.set_last_update_times(
            prices_dt=now,
            prices_source=source
        )

    except Exception as e:
        logger.exception(f"Error updating prices: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok", "message": "Prices updated successfully"})


@app.route("/alert-options", methods=["GET", "POST"])
def alert_options():
    try:
        if not os.path.exists(CONFIG_PATH):
            return jsonify({"error": "sonic_config.json not found"}), 404

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        config_data = AppConfig(**data)

        if request.method == "POST":
            new_low  = float(request.form.get("heat_index_low", 0.0))
            new_med  = float(request.form.get("heat_index_medium", 0.0))
            new_high = float(request.form.get("heat_index_high", 0.0))
            config_data.alert_ranges.heat_index_ranges.low = new_low
            config_data.alert_ranges.heat_index_ranges.medium = new_med
            config_data.alert_ranges.heat_index_ranges.high = new_high

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


@app.route("/show-updates")
def show_updates():
    data_locker = DataLocker(DB_PATH)
    times = data_locker.get_last_update_times()
    return render_template("some_template.html", update_times=times)


@app.route("/system-options", methods=["GET", "POST"])
def system_options():
    data_locker = DataLocker(DB_PATH)

    if request.method == "POST":
        config = load_app_config()
        form_action = request.form.get("action")

        if form_action == "reset_counters":
            data_locker.reset_api_counters()
            flash("API report counters have been reset!", "success")
            return redirect(url_for("system_options"))
        else:
            config.system_config.log_level = request.form.get("log_level", "INFO")
            config.system_config.db_path   = request.form.get("db_path", config.system_config.db_path)

            assets_str = request.form.get("assets", "")
            config.price_config.assets = [a.strip() for a in assets_str.split(",") if a.strip()]

            config.price_config.currency      = request.form.get("currency", "USD")
            config.price_config.fetch_timeout = int(request.form.get("fetch_timeout", 10))

            config.api_config.coingecko_api_enabled = request.form.get("coingecko_api_enabled", "ENABLE")
            config.api_config.binance_api_enabled   = request.form.get("binance_api_enabled", "ENABLE")
            config.api_config.coinmarketcap_api_key = request.form.get("coinmarketcap_api_key", "")

            save_app_config(config)

            flash("System options saved!", "success")
            return redirect(url_for("system_options"))

    config = load_app_config()
    return render_template("system_options.html", config=config)


@app.route("/export-config")
def export_config():
    return send_file(
        CONFIG_PATH,
        as_attachment=True,
        download_name="sonic_config.json",
        mimetype="application/json"
    )


@app.route("/heat", methods=["GET"])
def heat():
    data_locker = DataLocker(DB_PATH)
    calc_services = CalcServices()

    positions_data = data_locker.read_positions()
    positions_data = fill_positions_with_latest_price(positions_data)
    positions_data = calc_services.prepare_positions_for_display(positions_data)

    heat_data = build_heat_data(positions_data)
    return render_template("heat.html", heat_data=heat_data)


@app.route("/alerts")
def alerts():
    data_locker = DataLocker(DB_PATH)

    mini_prices = []
    for asset in ["BTC", "ETH", "SOL"]:
        row = data_locker.get_latest_price(asset)
        if row:
            mini_prices.append({
                "asset_type": row["asset_type"],
                "current_price": float(row["current_price"])
            })

    positions = data_locker.read_positions()
    config_dict = load_config(CONFIG_PATH, data_locker.get_db_connection())
    liquid_cfg = config_dict["alert_ranges"]["travel_percent_liquid_ranges"]

    low_thresh   = float(liquid_cfg["low"])
    med_thresh   = float(liquid_cfg["medium"])
    high_thresh  = float(liquid_cfg["high"])

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

    all_alerts = data_locker.get_alerts()
    active_alerts_data = []
    recent_alerts_data = []
    for alert in all_alerts:
        if alert.get("status", "").lower() == "active":
            active_alerts_data.append(alert)
        else:
            recent_alerts_data.append(alert)

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
    condition = request.form.get("condition", "ABOVE")

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
        "condition": condition,
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
    data_locker.create_alert(new_alert)

    flash("New alert created successfully!", "success")
    return redirect(url_for("alerts"))


@app.route("/manual_check_alerts", methods=["POST"])
def manual_check_alerts():
    manager.check_alerts()
    return jsonify({"status": "success", "message": "Alerts have been manually checked!"}), 200


@app.route("/jupiter-perps-proxy", methods=["GET"])
def jupiter_perps_proxy():
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
@app.route("/update_jupiter_positions", methods=["POST"])
def update_jupiter_positions():
    """
    Fetches all positions from Jupiter,
    inserts them if new,
    then sums *ALL* positions in the DB for the 'brokerage' value,
    and updates system_vars accordingly.
    """
    data_locker = DataLocker(db_path=DB_PATH)
    try:
        wallets_list = data_locker.read_wallets()
        if not wallets_list:
            app.logger.info("No wallets found in DB.")
            return jsonify({"message": "No wallets found in DB"}), 200

        total_positions_imported = 0

        # 1) For each wallet, fetch Jupiter positions
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

            # 2) Build position dicts
            new_positions = []
            for item in data_list:
                try:
                    epoch_time = float(item.get("updatedTime", 0))
                    updated_dt = datetime.fromtimestamp(epoch_time)
                    mint = item.get("marketMint", "")
                    asset_type = MINT_TO_ASSET.get(mint, "BTC")  # default to BTC if unknown
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
                        "wallet_name": w["name"],  # link it to the wallet
                    }
                    new_positions.append(pos_dict)
                except Exception as map_err:
                    app.logger.warning(
                        f"Skipping item for wallet {w['name']} due to mapping error: {map_err}"
                    )

            # 3) Insert if not duplicate
            for p in new_positions:
                dup_count = data_locker.cursor.execute("""
                    SELECT COUNT(*) FROM positions
                     WHERE wallet_name = ?
                       AND asset_type = ?
                       AND position_type = ?
                       AND ABS(size - ?) < 0.000001
                       AND ABS(collateral - ?) < 0.000001
                       AND last_updated = ?
                """, (
                    p["wallet_name"],
                    p["asset_type"],
                    p["position_type"],
                    p["size"],
                    p["collateral"],
                    p["last_updated"]
                )).fetchone()

                if dup_count[0] == 0:
                    data_locker.create_position(p)
                    total_positions_imported += 1
                else:
                    app.logger.info(f"Skipping duplicate Jupiter position {p}")

        # 4) Since all positions in the DB are from Jupiter,
        #    sum the 'value' of ALL positions in 'positions' for total_brokerage_balance.
        all_positions = data_locker.get_positions()  # read ALL from the DB
        total_brokerage_value = sum(pos["value"] for pos in all_positions)

        # 5) Read the existing wallet balance from system_vars
        balance_vars = data_locker.get_balance_vars()
        old_wallet_balance = balance_vars["total_wallet_balance"]  # or 0.0 if none

        # 6) total_balance => sum of total_brokerage_balance + total_wallet_balance
        new_total_balance = old_wallet_balance + total_brokerage_value

        # 7) Save them in system_vars
        data_locker.set_balance_vars(
            brokerage_balance=total_brokerage_value,
            total_balance=new_total_balance
        )

        msg = (f"Imported {total_positions_imported} new Jupiter position(s). "
               f"BrokerageBalance={total_brokerage_value:.2f}, "
               f"TotalBalance={new_total_balance:.2f}")
        app.logger.info(msg)

        return jsonify({"message": msg}), 200

    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error fetching Jupiter: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/delete-all-jupiter-positions", methods=["POST"])
def delete_all_jupiter_positions():
    data_locker = DataLocker(DB_PATH)
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
    1) delete_all_positions
    2) update_jupiter_positions
    3) update_prices
    Then sets last_update_time_positions + last_update_time_prices,
    plus last_update_positions_source + last_update_prices_source.
    """
    # Read 'source' or default to "API"
    source = request.args.get("source") or request.form.get("source") or "API"

    data_locker = DataLocker(DB_PATH)

    # 1) Remove old positions
    delete_all_positions()  # or data_locker.delete_all_positions()

    # 2) Update from Jupiter
    jupiter_resp, jupiter_code = update_jupiter_positions()
    if jupiter_code != 200:
        return jupiter_resp, jupiter_code

    # 3) Update prices
    prices_resp = update_prices()
    if prices_resp.status_code != 200:
        return prices_resp

    # Set the last-update timestamps & sources
    now = datetime.now()
    data_locker.set_last_update_times(
        positions_dt=now,
        positions_source=source,  # <--- store the positions source
        prices_dt=now,
        prices_source=source      # <--- store the prices source
    )

    return jsonify({
        "message": f"Jupiter positions + Prices updated successfully by {source}!",
        "source": source,
        "last_update_time_positions": now.isoformat(),
        "last_update_time_prices": now.isoformat()
    }), 200


@app.route("/audio-tester")
def audio_tester():
    return render_template("audio_tester.html")


@app.route("/api/positions_data", methods=["GET"])
def positions_data_api():
    data_locker = DataLocker(DB_PATH)
    calc_services = CalcServices()

    mini_prices = []
    for asset in ["BTC", "ETH", "SOL"]:
        row = data_locker.get_latest_price(asset)
        if row:
            mini_prices.append({
                "asset_type": row["asset_type"],
                "current_price": float(row["current_price"])
            })

    positions_data = data_locker.read_positions()
    positions_data = fill_positions_with_latest_price(positions_data)
    updated_positions = calc_services.aggregator_positions(positions_data, DB_PATH)

    for pos in updated_positions:
        pos["collateral"] = float(pos.get("collateral") or 0.0)
        wallet_name = pos.get("wallet_name")
        if wallet_name:
            w = data_locker.get_wallet_by_name(wallet_name)
            pos["wallet_name"] = w
        else:
            pos["wallet_name"] = None

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

    hi_cfg   = alert_dict.get("heat_index_ranges", {})
    hi_low   = hi_cfg.get("low", 0.0)
    hi_med   = hi_cfg.get("medium", 0.0)
    hi_high  = hi_cfg.get("high", None)

    coll_cfg = alert_dict.get("collateral_ranges", {})
    coll_low   = coll_cfg.get("low", 0.0)
    coll_med   = coll_cfg.get("medium", 0.0)
    coll_high  = coll_cfg.get("high", None)

    for pos in updated_positions:
        heat_val = float(pos.get("heat_index", 0.0))
        pos["heat_alert_class"] = get_alert_class(heat_val, hi_low, hi_med, hi_high)

        coll_val = float(pos.get("collateral", 0.0))
        pos["collateral_alert_class"] = get_alert_class(coll_val, coll_low, coll_med, coll_high)

    totals_dict = calc_services.calculate_totals(updated_positions)

    return jsonify({
        "mini_prices": mini_prices,
        "positions": updated_positions,
        "totals": totals_dict
    })


@app.route("/hedge-report")
def hedge_report():
    return render_template("hedge_report.html")


@app.route("/database-viewer")
def database_viewer():
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


##################################################
# Utility
##################################################

def load_app_config() -> AppConfig:
    if not os.path.exists(CONFIG_PATH):
        return AppConfig()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return AppConfig(**data)

def save_app_config(config: AppConfig):
    data = config.model_dump()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def fill_positions_with_latest_price(positions: List[dict]) -> List[dict]:
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

    return structure

@app.route("/assets")
@app.route("/assets")
def assets():
    data_locker = DataLocker(DB_PATH)

    # 1) Read brokers & sort descending
    brokers = data_locker.read_brokers()
    brokers.sort(key=lambda b: b["total_holding"], reverse=True)

    # 2) Read wallets & sort descending
    wallets = data_locker.read_wallets()
    wallets.sort(key=lambda w: w["balance"], reverse=True)

    # 3) Read new system_vars balances
    balance_dict = data_locker.get_balance_vars()  # e.g. {"total_brokerage_balance":..., "total_wallet_balance":..., "total_balance":...}

    # If you need to do any calculations or merges, do it here

    return render_template(
        "assets.html",
        brokers=brokers,
        wallets=wallets,
        total_brokerage_balance=balance_dict["total_brokerage_balance"],
        total_balance=balance_dict["total_balance"],
        total_wallet_balance=balance_dict["total_wallet_balance"]
    )


@app.route("/add_wallet", methods=["POST"])
def add_wallet():
    """
    Reads form fields for a new wallet, then inserts a row via data_locker.create_wallet().
    Redirects back to /assets.
    """
    data_locker = DataLocker(DB_PATH)

    name = request.form.get("name", "").strip()
    public_addr = request.form.get("public_address", "").strip()
    private_addr = request.form.get("private_address", "").strip()
    image_path = request.form.get("image_path", "").strip()
    balance_str = request.form.get("balance", "0.0").strip()

    try:
        balance_val = float(balance_str)
    except ValueError:
        balance_val = 0.0

    wallet_dict = {
        "name": name,
        "public_address": public_addr,
        "private_address": private_addr,
        "image_path": image_path,
        "balance": balance_val
    }
    data_locker.create_wallet(wallet_dict)

    flash(f"Wallet '{name}' added!", "success")
    return redirect(url_for("assets"))


@app.route("/add_broker", methods=["POST"])
def add_broker():
    """
    Reads form fields for a new broker, then inserts a row in 'brokers'.
    Redirects back to /assets.
    """
    data_locker = DataLocker(DB_PATH)

    name = request.form.get("name", "").strip()
    image_path = request.form.get("image_path", "").strip()
    web_address = request.form.get("web_address", "").strip()
    holding_str = request.form.get("total_holding", "0.0").strip()

    try:
        holding_val = float(holding_str)
    except ValueError:
        holding_val = 0.0

    broker_dict = {
        "name": name,
        "image_path": image_path,
        "web_address": web_address,
        "total_holding": holding_val
    }
    data_locker.create_broker(broker_dict)

    flash(f"Broker '{name}' added!", "success")
    return redirect(url_for("assets"))


@app.route("/delete_wallet/<wallet_name>", methods=["POST"])
def delete_wallet(wallet_name):
    """
    Removes the given wallet row from DB by name.
    (If your 'wallets' table uses a different primary key, adjust accordingly.)
    """
    data_locker = DataLocker(DB_PATH)
    conn = data_locker.get_db_connection()
    cursor = conn.cursor()

    # If 'name' is the PK/unique:
    cursor.execute("DELETE FROM wallets WHERE name = ?", (wallet_name,))
    conn.commit()

    flash(f"Deleted wallet '{wallet_name}'.", "info")
    return redirect(url_for("assets"))


@app.route("/delete_broker/<broker_name>", methods=["POST"])
def delete_broker(broker_name):
    """
    Removes the given broker row by its 'name' primary key.
    """
    data_locker = DataLocker(DB_PATH)
    conn = data_locker.get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM brokers WHERE name = ?", (broker_name,))
    conn.commit()

    flash(f"Deleted broker '{broker_name}'.", "info")
    return redirect(url_for("assets"))

@app.route("/theme_options")
def theme_options():
    # Renders the new template
    return render_template("theme_options.html")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
