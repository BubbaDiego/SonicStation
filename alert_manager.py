import os
import time
import json
import smtplib
import logging
import sqlite3
from email.mime.text import MIMEText
from typing import Dict, Any, List

# Adjust imports to your actual modules
from data_locker import DataLocker
from calc_services import CalcServices
from config_manager import load_config

logger = logging.getLogger("AlertManagerLogger")
logger.setLevel(logging.DEBUG)

class AlertManager:
    def __init__(
        self,
        db_path: str = "mother_brain.db",
        poll_interval: int = 60,
        config_path: str = "sonic_config.json"
    ):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.config_path = config_path

        # Setup
        self.data_locker = DataLocker(self.db_path)
        self.calc_services = CalcServices()

        # Load config (a dict)
        db_conn = self.data_locker.get_db_connection()
        self.config = load_config(self.config_path, db_conn)

        # e.g. {"low": -25.0, "medium": -50.0, "high": -75.0}
        self.liquid_cfg = self.config["alert_ranges"]["travel_percent_liquid_ranges"]

        # If you have "notification_config" in the dict:
        self.email_conf = self.config["notification_config"]["email"]
        self.sms_conf = self.config["notification_config"]["sms"]

        # The global alert cooldown in seconds (default 900 = 15 mins)
        self.cooldown = self.config.get("alert_cooldown_seconds", 900)

        # "alert_monitor_enabled": true,
        self.monitor_enabled = self.config["system_config"].get("alert_monitor_enabled", True)

        # We store times of last triggers to enforce cooldown
        self.last_triggered: Dict[str, float] = {}

        logger.info(
            "AlertManagerV2 started. poll_interval=%s, cooldown=%s",
            poll_interval,
            self.cooldown
        )

    def run(self):
        """
        Continuously checks alerts on a loop, every self.poll_interval seconds.
        """
        while True:
            self.check_alerts()
            time.sleep(self.poll_interval)

    def check_alerts(self):
        """
        Called periodically or on-demand (like from your /manual_check_alerts route).
        Checks both Travel% alerts and PriceThreshold alerts.
        """
        if not self.monitor_enabled:
            logger.debug("Alert monitoring disabled. Skipping.")
            return

        # 1) Travel% check
        positions = self.data_locker.read_positions()
        logger.debug("Loaded %d positions for TravelPercent checks.", len(positions))

        for pos in positions:
            self.check_travel_percent_liquid(pos)

        # 2) PriceThreshold check
        self.check_price_alerts()

    def check_travel_percent_liquid(self, pos: Dict[str, Any]):
        """
        If current_travel_percent is negative and passes certain thresholds
        (like -25, -50, -75), we send an alert. This logic references self.liquid_cfg.
        """
        val = float(pos.get("current_travel_percent", 0.0))
        if val >= 0:
            return  # skip if not negative

        pos_id = pos.get("id", "unknown")
        asset = pos.get("asset_type", "???")

        # e.g. {"low": -25, "medium": -50, "high": -75}
        low = self.liquid_cfg["low"]
        medium = self.liquid_cfg["medium"]
        high = self.liquid_cfg["high"]

        # figure out if val is in HIGH, MEDIUM, LOW zone
        alert_level = None
        if val <= high:
            alert_level = "HIGH"
        elif val <= medium:
            alert_level = "MEDIUM"
        elif val <= low:
            alert_level = "LOW"
        else:
            return  # -10, for example, doesn't cross any threshold

        # cooldown check
        key = f"{pos_id}-{alert_level}"
        now = time.time()
        last_time = self.last_triggered.get(key, 0)
        if (now - last_time) < self.cooldown:
            logger.debug(
                "Skipping repeated TravelPercent alert for %s => %s (cooldown).",
                pos_id,
                alert_level
            )
            return

        self.last_triggered[key] = now

        message = (
            f"Travel Percent Liquid ALERT\n"
            f"Position ID: {pos_id}, Asset: {asset}\n"
            f"Current Travel%={val:.2f}% => {alert_level} zone."
        )
        logger.info("Triggering Travel%% alert => %s", message)

        self.send_email(message)
        self.send_sms(message)

    def check_price_alerts(self):
        """
        Fetch all active PRICE_THRESHOLD alerts from the DB.
        For each alert, read the current price from 'prices' table,
        compare, and trigger if condition is met.
        """
        all_alerts = self.data_locker.get_alerts()
        price_alerts = [
            a for a in all_alerts
            if a.get("alert_type") == "PRICE_THRESHOLD"
            and a.get("status", "").lower() == "active"
        ]

        for alert in price_alerts:
            asset = alert.get("asset_type", "BTC")
            trigger_val = float(alert.get("trigger_value", 0.0))

            # The new field that decides "ABOVE" vs "BELOW" logic
            condition = alert.get("condition", "ABOVE").upper()

            # Grab latest price from DB
            price_info = self.data_locker.get_latest_price(asset)
            if not price_info:
                continue  # no price data, skip

            current_price = float(price_info["current_price"])
            # handle "above" or "below"
            if condition == "ABOVE":
                # e.g. if current >= trigger => alert
                if current_price >= trigger_val:
                    self.handle_price_alert_trigger(alert, current_price)
            else:  # "BELOW"
                # e.g. if current <= trigger => alert
                if current_price <= trigger_val:
                    self.handle_price_alert_trigger(alert, current_price)

    def handle_price_alert_trigger(self, alert: dict, current_price: float):
        """
        Called when a price threshold is met. We check cooldowns, then send notification.
        """
        alert_id = alert["id"]
        key = f"price-alert-{alert_id}"
        now = time.time()
        last_time = self.last_triggered.get(key, 0)
        if (now - last_time) < self.cooldown:
            logger.debug("Skipping repeated Price alert for %s (cooldown).", alert_id)
            return

        self.last_triggered[key] = now

        asset = alert.get("asset_type", "BTC")
        cond = alert.get("condition", "ABOVE").upper()
        trig_val = float(alert.get("trigger_value", 0.0))

        message = (
            f"Price ALERT\n"
            f"Alert ID: {alert_id}\n"
            f"Asset: {asset}\n"
            f"Condition: {cond}\n"
            f"Trigger Value: {trig_val}\n"
            f"Current Price: {current_price}\n"
        )
        logger.info("Triggering PriceThreshold alert => %s", message)

        # Depending on notification_type, send something
        notif_type = alert.get("notification_type", "SMS").upper()
        if notif_type == "EMAIL":
            self.send_email(message)
        elif notif_type == "SMS":
            self.send_sms(message)
        else:
            logger.info("Alert with type=%s doesn't require direct messaging.", notif_type)

    def send_email(self, body: str):
        """
        Basic email send. Adjust fields or exception handling as needed.
        """
        try:
            smtp_server = self.email_conf["smtp_server"]
            port = int(self.email_conf["smtp_port"])
            user = self.email_conf["smtp_user"]
            password = self.email_conf["smtp_password"]
            recipient = self.email_conf["recipient_email"]

            msg = MIMEText(body)
            msg["Subject"] = "Sonic Alert"
            msg["From"] = user
            msg["To"] = recipient

            with smtplib.SMTP(smtp_server, port) as server:
                server.ehlo()
                server.starttls()
                server.login(user, password)
                server.sendmail(user, [recipient], msg.as_string())

            logger.info("Email alert sent to %s", recipient)
        except Exception as e:
            logger.error("Failed to send email: %s", e, exc_info=True)

    def send_sms(self, body: str):
        """
        Basic SMS send via email-to-SMS gateway.
        """
        gateway = self.sms_conf["carrier_gateway"]
        number = self.sms_conf["recipient_number"]
        sms_address = f"{number}@{gateway}"

        smtp_server = self.email_conf["smtp_server"]
        port = int(self.email_conf["smtp_port"])
        user = self.email_conf["smtp_user"]
        password = self.email_conf["smtp_password"]

        msg = MIMEText(body)
        msg["Subject"] = "Sonic Price/Travel Alert"
        msg["From"] = user
        msg["To"] = sms_address

        try:
            with smtplib.SMTP(smtp_server, port) as server:
                server.ehlo()
                server.starttls()
                server.login(user, password)
                server.sendmail(user, [sms_address], msg.as_string())

            logger.info("SMS alert sent to %s", sms_address)
        except Exception as e:
            logger.error("Failed to send SMS: %s", e, exc_info=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    manager = AlertManager(
        db_path=os.path.abspath("mother_brain.db"),
        poll_interval=60,
        config_path="sonic_config.json"
    )
    manager.run()
