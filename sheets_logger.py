"""
Log Zepto order items to Google Sheets for analytics.
Uses a service account for auth — share the sheet with the service account email.
"""

import os
import json
import logging
from datetime import datetime

import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsLogger:
    def __init__(self):
        self.spreadsheet_id = os.environ["GOOGLE_SHEET_ID"]
        self.creds = None
        self._auth()

    def _auth(self):
        """Authenticate using service account credentials."""
        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if creds_json:
            creds_info = json.loads(creds_json)
            self.creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        else:
            creds_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
            self.creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)

    def _get_headers(self):
        """Get auth headers, refreshing token if needed."""
        if not self.creds.valid:
            self.creds.refresh(Request())
        return {"Authorization": f"Bearer {self.creds.token}"}

    def append_rows(self, rows: list[list]):
        """Append rows to the sheet."""
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            f"/values/Sheet1!A:H:append"
            f"?valueInputOption=USER_ENTERED"
            f"&insertDataOption=INSERT_ROWS"
        )
        body = {"values": rows}
        r = requests.post(url, headers=self._get_headers(), json=body)
        r.raise_for_status()
        logger.info(f"Logged {len(rows)} rows to Google Sheets")
        return r.json()

    def ensure_headers(self):
        """Add header row if sheet is empty."""
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            f"/values/Sheet1!A1:H1"
        )
        r = requests.get(url, headers=self._get_headers())
        r.raise_for_status()
        data = r.json()

        if "values" not in data:
            headers = [
                ["Date", "Order ID", "Item", "Category", "Amount (₹)",
                 "Tagged To", "Split Type", "Person's Share (₹)"]
            ]
            self.append_rows(headers)
            logger.info("Added header row to sheet")


def log_order_to_sheets(sheets: SheetsLogger, parsed: dict, split: dict):
    """
    Log all items from a processed order to Google Sheets.
    Each item gets a row with its category, who it's tagged to, and the share amount.
    """
    from categorizer import categorize_item

    order_date = parsed.get("order_date", datetime.now().strftime("%d-%m-%Y"))
    order_id = parsed.get("order_no", "")

    rows = []

    for item in split["my_items"]:
        rows.append([
            order_date, order_id, item["name"],
            categorize_item(item["name"]),
            item["amount"],
            "Tanmay", "Personal", item["amount"],
        ])

    for item in split["kalash_items"]:
        rows.append([
            order_date, order_id, item["name"],
            categorize_item(item["name"]),
            item["amount"],
            "Kalash", "Personal", item["amount"],
        ])

    for item in split.get("abhirag_items", []):
        rows.append([
            order_date, order_id, item["name"],
            categorize_item(item["name"]),
            item["amount"],
            "Abhirag", "Personal", item["amount"],
        ])

    for item in split["shared_items"]:
        # Log shared items twice — once for each person's share
        category = categorize_item(item["name"])
        half = round(item["amount"] / 2, 2)
        rows.append([
            order_date, order_id, item["name"],
            category, item["amount"],
            "Tanmay", "Shared", half,
        ])
        rows.append([
            order_date, order_id, item["name"],
            category, item["amount"],
            "Kalash", "Shared", half,
        ])

    if rows:
        sheets.append_rows(rows)

    return len(rows)
