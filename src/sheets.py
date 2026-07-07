"""Google Sheets integration using gspread with a service account JSON key."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_gspread_client(credentials_path: str) -> gspread.Client:
    """Authenticate and return a gspread client using a service account JSON key.

    Parameters
    ----------
    credentials_path:
        Path to the service account JSON key file.

    Returns
    -------
    gspread.Client
        An authorized gspread client.
    """
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return gspread.authorize(creds)


def write_results_to_sheet(
    credentials_path: str,
    spreadsheet_id: str,
    results: list[dict[str, Any]],
    sheet_name: str = "Sheet1",
) -> None:
    """Write business results to a Google Sheet.

    Parameters
    ----------
    credentials_path:
        Path to the service account JSON key file.
    spreadsheet_id:
        The Google Sheets document ID (found in the spreadsheet URL).
    results:
        List of dicts with keys like business_name, rating, website, email.
    sheet_name:
        Name of the worksheet tab to write to.

    Raises
    ------
    gspread.exceptions.SpreadsheetNotFound
        If the spreadsheet ID does not exist or the service account lacks access.
    gspread.exceptions.APIError
        On API-level errors.
    """
    if not results:
        logging.warning("No results to write to Google Sheets.")
        return

    client = _get_gspread_client(credentials_path)

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
    except gspread.exceptions.SpreadsheetNotFound as exc:
        logging.error("Spreadsheet not found: %s", spreadsheet_id)
        raise

    # Find or create the target worksheet
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)

    # Build the data grid from dict keys, converting lists to comma-separated strings
    headers = list(results[0].keys())
    rows: list[list[str]] = [headers]
    for item in results:
        rows.append([_cell_value(item.get(h)) for h in headers])

    worksheet.clear()
    worksheet.update("A1", rows)


def _cell_value(value: object) -> str:
    """Convert a value to a string suitable for a spreadsheet cell.

    Lists are joined with ', ' to prevent iterating over characters.
    Everything else becomes str(value) or empty string.
    """
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value else ""


def send_result_to_sheet(
    webhook_url: str,
    headers: list[str],
    values: dict[str, str],
    timeout: int = 30,
    extra_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Post a single row of data to a Google Sheet via an Apps Script Web App URL.

    Parameters
    ----------
    webhook_url:
        The Apps Script Web App URL (from Deploy > Web app).
    headers:
        Ordered list of column header names.
    values:
        Dictionary mapping each column header to its string value.
    timeout:
        Request timeout in seconds.
    extra_payload:
        Optional additional fields merged into the JSON payload
        (e.g. ``{"extra_key": "extra_value"}``).  The function itself is agnostic
        to the content — it is forwarded verbatim to the webhook.

    Returns
    -------
    dict
        The parsed JSON response from the webhook.

    Raises
    ------
    requests.exceptions.RequestException
        On network or HTTP errors.
    """
    payload: dict[str, object] = {"headers": headers, "values": values}
    if extra_payload:
        payload.update(extra_payload)
    response = requests.post(webhook_url, json=payload, timeout=timeout)
    response.raise_for_status()
    return dict(response.json())


def download_processed_cache(
    credentials_path: str,
    spreadsheet_id: str,
) -> tuple[set[str], set[str]]:
    """Load processed business names and websites from Accepted + Rejected sheets.

    Reads the *Accepted* (falling back to *Sheet1*) and *Rejected* sheets from
    the given spreadsheet and returns dedup sets used to skip already-processed
    businesses on subsequent runs.

    If the *Rejected* sheet does not exist it is created with fixed headers so the
    first reject write has a header row.

    Parameters
    ----------
    credentials_path:
        Path to the service account JSON key file.
    spreadsheet_id:
        The Google Sheets document ID.

    Returns
    -------
    tuple[set[str], set[str]]
        ``(seen_names, seen_websites)`` — both lowercase, stripped.
        Websites are **not** normalized here; the caller normalizes via
        ``_normalize_website()``.
    """
    client = _get_gspread_client(credentials_path)
    spreadsheet = client.open_by_key(spreadsheet_id)

    seen_names: set[str] = set()
    seen_websites: set[str] = set()

    # --- Read Accepted sheet (try "Accepted", fall back to "Sheet1") ---
    accepted_sheet_name = "Accepted"
    try:
        leads_ws = spreadsheet.worksheet(accepted_sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        accepted_sheet_name = "Sheet1"
        try:
            leads_ws = spreadsheet.worksheet(accepted_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            leads_ws = None

    if leads_ws is not None:
        leads_rows = leads_ws.get_all_values()
        if leads_rows:
            header_row = [h.strip().lower() for h in leads_rows[0]]
            # Try header-name matching; fall back to position-based (col 0, col 1)
            name_col = -1
            for candidate in ("company name", "business name"):
                try:
                    name_col = header_row.index(candidate)
                    break
                except ValueError:
                    continue
            if name_col < 0 and len(header_row) >= 1:
                name_col = 0  # fallback: first column

            website_col = -1
            for candidate in ("website", "website url", "web", "site"):
                try:
                    website_col = header_row.index(candidate)
                    break
                except ValueError:
                    continue
            if website_col < 0 and len(header_row) >= 2:
                website_col = 1  # fallback: second column

            for row in leads_rows[1:]:
                if name_col >= 0 and name_col < len(row):
                    name = row[name_col].strip().lower()
                    if name:
                        seen_names.add(name)
                if website_col >= 0 and website_col < len(row):
                    site = row[website_col].strip().lower()
                    if site:
                        seen_websites.add(site)
    # --- Read Rejected sheet ---
    try:
        rejected_ws = spreadsheet.worksheet("Rejected")
        rejected_rows = rejected_ws.get_all_values()
        for row in rejected_rows[1:]:  # skip header
            if len(row) >= 2:
                name = row[0].strip().lower() if row[0] else ""
                site = row[1].strip().lower() if row[1] else ""
                if name:
                    seen_names.add(name)
                if site:
                    seen_websites.add(site)
    except gspread.exceptions.WorksheetNotFound:
        pass  # Rejected sheet may not exist yet; append_rejected() will create it

    return seen_names, seen_websites


def append_rejected(
    credentials_path: str,
    spreadsheet_id: str,
    business_name: str,
    website: str,
    search_location: str,
    reject_reason: str,
    columns: list[dict[str, str]] | None = None,
    field_values: dict[str, str] | None = None,
) -> None:
    """Append a rejected lead to the Rejected sheet.

    When *columns* and *field_values* are provided, the Rejected sheet mirrors
    the same column structure as the Accepted sheet with an additional
    *Reject Reason* column appended at the end.  This makes both tabs look
    alike for easy comparison.

    Parameters
    ----------
    credentials_path:
        Path to the service account JSON key file.
    spreadsheet_id:
        The Google Sheets document ID.
    business_name:
    website:
    search_location:
    reject_reason:
        Short human-readable reason (e.g. ``"Missing Required Field(s): email"``).
    columns:
        Same column config as used for the Accepted webhook payload.
    field_values:
        Same field-values dict as used for the Accepted webhook payload.
    """
    client = _get_gspread_client(credentials_path)
    spreadsheet = client.open_by_key(spreadsheet_id)

    if columns and field_values:
        # --- Rich row: same columns as Accepted + Reject Reason ---
        rich_headers = [str(col["header"]) for col in columns] + ["Reject Reason"]
        rich_row = [field_values.get(str(col["field"]), "") for col in columns] + [reject_reason]
        try:
            worksheet = spreadsheet.worksheet("Rejected")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="Rejected", rows=1000, cols=len(rich_headers))
            worksheet.append_row(rich_headers)
        else:
            # Ensure headers match the rich schema
            existing = worksheet.get_all_values()
            if not existing:
                worksheet.append_row(rich_headers)
            elif existing[0] != rich_headers:
                # Replace header row with correct rich schema
                for i, h in enumerate(rich_headers):
                    worksheet.update_cell(1, i + 1, h)
                # Clear any stale header cells beyond the new schema
                for i in range(len(rich_headers), len(existing[0])):
                    worksheet.update_cell(1, i + 1, "")
        worksheet.append_row(rich_row)
    else:
        # --- Legacy: minimal columns ---
        try:
            worksheet = spreadsheet.worksheet("Rejected")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="Rejected", rows=1000, cols=5)
            worksheet.append_row(["Business Name", "Website", "Search Location", "Reject Reason", "Date Added"])

        worksheet.append_row([
            business_name,
            website,
            search_location,
            reject_reason,
            datetime.now().isoformat(),
        ])


def write_summary_sheet(
    credentials_path: str,
    spreadsheet_id: str,
    summary_data: list[dict[str, str]],
) -> None:
    """Create or overwrite a 'Summary' tab in the spreadsheet.

    Parameters
    ----------
    credentials_path:
        Path to the service account JSON key file.
    spreadsheet_id:
        The Google Sheets document ID.
    summary_data:
        List of dicts, each with ``Label`` and ``Value`` keys.
    """
    if not summary_data:
        return

    client = _get_gspread_client(credentials_path)
    spreadsheet = client.open_by_key(spreadsheet_id)

    # Remove existing Summary tab if present, then create a fresh one
    try:
        existing = spreadsheet.worksheet("Summary")
        spreadsheet.del_worksheet(existing)
    except gspread.exceptions.WorksheetNotFound:
        pass

    worksheet = spreadsheet.add_worksheet(title="Summary", rows=len(summary_data) + 5, cols=3)

    headers = list(summary_data[0].keys())
    rows: list[list[str]] = [headers]
    for item in summary_data:
        rows.append([_cell_value(item.get(h)) for h in headers])

    worksheet.update("A1", rows)
    worksheet.format("A1:B1", {"textFormat": {"bold": True}})
