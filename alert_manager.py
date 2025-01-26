# alert_manager.py
import os
import time
import json
import smtplib
import logging
import sqlite3
from email.mime.text import MIMEText
from typing import Dict, Any, List
from data_locker import DataLocker
from calc_services import CalcServices
from config_manager import load_config

logger = logging.getLogger("AlertManagerLogger")
logger.setLevel(logging.DEBUG)


class AlertManager:
    def __init__(self,
                 db_path=r"mother_brain.db",
                 poll_interval: int = 60,
                 config_path: str = "sonic_config.json"):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.config_path = config_path

        # Setup
        self.data_locker = DataLocker(self.db_path)
        self.calc_services = CalcServices()

        # This returns a dict, not an object:
        db_conn = self.data_locker.get_db_connection()
        self.config = load_config(self.config_path, db_conn)  # <-- returns dict

        # travel_percent_liquid_ranges:
        self.liquid_cfg = self.config["alert_ranges"]["travel_percent_liquid_ranges"]

        # If you have "notification_config" in the dict:
        self.email_conf = self.config["notification_config"]["email"]
        self.sms_conf = self.config["notification_config"]["sms"]

        # If you stored alert_cooldown_seconds in the top-level dict:
        self.cooldown = self.config.get("alert_cooldown_seconds", 900)  # 15 mins default

        # We'll rely on system_config for a boolean:
        # "alert_monitor_enabled": true,
        self.monitor_enabled = self.config["system_config"].get("alert_monitor_enabled", True)

        self.last_triggered: Dict[str, float] = {}
        logger.info("AlertManagerV2 started. poll_interval=%s, cooldown=%s", poll_interval, self.cooldown)

    def run(self):
        while True:
            self.check_alerts()
            time.sleep(self.poll_interval)

    def check_alerts(self):
        if not self.monitor_enabled:
            logger.debug("Alert monitoring disabled. Skipping.")
            return

        positions = self.data_locker.read_positions()
        logger.debug("Loaded %d positions for TravelPercent checks.", len(positions))

        for pos in positions:
            self.check_travel_percent_liquid(pos)

    def check_travel_percent_liquid(self, pos: Dict[str, Any]):
        val = float(pos.get("current_travel_percent", 0.0))
        if val >= 0:
            return  # skip

        pos_id = pos.get("id", "unknown")
        asset = pos.get("asset_type", "???")

        # e.g. liquid_cfg = {"low": -25.0, "medium": -50.0, "high": -75.0}
        low = self.liquid_cfg["low"]
        medium = self.liquid_cfg["medium"]
        high = self.liquid_cfg["high"]

        # if val <= -75 => HIGH
        # elif val <= -50 => MEDIUM
        # elif val <= -25 => LOW
        # else => no alert
        alert_level = None
        if val <= high:
            alert_level = "HIGH"
        elif val <= medium:
            alert_level = "MEDIUM"
        elif val <= low:
            alert_level = "LOW"
        else:
            return

        # cooldown check
        key = f"{pos_id}-{alert_level}"
        now = time.time()
        last_time = self.last_triggered.get(key, 0)
        if (now - last_time) < self.cooldown:
            logger.debug("Skipping repeated alert for %s => %s (cooldown).", pos_id, alert_level)
            return

        self.last_triggered[key] = now

        message = (f"Travel Percent Liquid ALERT\n"
                   f"Position ID: {pos_id}, Asset: {asset}\n"
                   f"Current Travel%={val:.2f}% => {alert_level} zone.")
        logger.info("Triggering Travel%% alert => %s", message)

        self.send_email(message)
        self.send_sms(message)

    def send_email(self, body: str):
        try:
            smtp_server = self.email_conf["smtp_server"]
            port = int(self.email_conf["smtp_port"])
            user = self.email_conf["smtp_user"]
            password = self.email_conf["smtp_password"]
            recipient = self.email_conf["recipient_email"]

            msg = MIMEText(body)
            msg["Subject"] = "Sonic TravelPercent Alert"
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
        gateway = self.sms_conf["carrier_gateway"]
        number = self.sms_conf["recipient_number"]
        sms_address = f"{number}@{gateway}"

        smtp_server = self.email_conf["smtp_server"]
        port = int(self.email_conf["smtp_port"])
        user = self.email_conf["smtp_user"]
        password = self.email_conf["smtp_password"]

        msg = MIMEText(body)
        msg["Subject"] = "Sonic TravelPercent Alert (SMS)"
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
