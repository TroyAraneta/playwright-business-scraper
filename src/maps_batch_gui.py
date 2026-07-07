from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as filedialog
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import requests
from app import scrape_company
# Import the bulk scraper that runs the per‑website work in a thread pool.
# It lives in the same module that provides ``collect_websites_from_google_maps``.
from maps_website_collector import scrape_websites
from maps_website_collector import collect_websites_from_google_maps
from sheets import append_rejected, download_processed_cache, send_result_to_sheet, write_summary_sheet


# ---- Windows power-management API (keep screen awake) ----
if hasattr(ctypes, "windll"):
    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001
    _ES_DISPLAY_REQUIRED = 0x00000002
    _SetThreadExecutionState = ctypes.windll.kernel32.SetThreadExecutionState
else:
    _SetThreadExecutionState = lambda _: None  # no-op on non-Windows


def _prevent_sleep() -> None:
    """Tell Windows the app is busy: prevent display-off and system sleep."""
    _SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)


def _restore_sleep() -> None:
    """Revert to the user's normal power settings."""
    _SetThreadExecutionState(_ES_CONTINUOUS)


if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent.parent.parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = PROJECT_ROOT / "logs" / "maps_batch_settings.json"
SCRAPER_SETTINGS_FILE = PROJECT_ROOT / "logs" / "scraper_settings.json"

FIELD_OPTIONS = {
    "maps_business_name": "Google Maps Business Name",
    "source_url": "Website URL",
    "rating": "Rating (stars)",
    "review_count": "Review Count",
    "company_name": "Scraped Company Name",
    "email": "Company Email(s)",
    "phone": "Phone Number(s)",
    "location": "Location",
    "services": "Services Offered",
    "linkedin": "LinkedIn Profile",
    "facebook": "Facebook Page",
    "instagram": "Instagram Profile",
    "twitter": "Twitter/X Profile",
    "youtube": "YouTube Channel",
    "tiktok": "TikTok Profile",
    "all_socials": "All Social Links (comma-sep)",
    "timestamp": "Scraped Timestamp",
    "maps_url": "Google Maps URL",
    "error": "Error",
}

REVERSE_FIELD_OPTIONS = {label: code for code, label in FIELD_OPTIONS.items()}

DEFAULT_COLUMNS = [
    {"header": "Company Name", "field": "maps_business_name"},
    {"header": "Website", "field": "source_url"},
    {"header": "Company Email", "field": "email"},
    {"header": "Phone", "field": "phone"},
    {"header": "Location", "field": "location"},
    {"header": "Services", "field": "services"},
    {"header": "Google Maps URL", "field": "maps_url"},
    {"header": "Error", "field": "error"},
]


def load_settings() -> dict[str, object]:
    data: dict[str, object] = {}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if "columns" not in data:
                data["columns"] = DEFAULT_COLUMNS
        except Exception:
            data = {}

    # Backward compat: if a single "location" was saved, seed the queue with it
    if not data.get("location_queue") and data.get("location"):
        data["location_queue"] = [str(data["location"])]

    # Fill missing fields with defaults
    defaults = {"query": "marketing agency", "location_queue": ["Austin Texas"], "max_results": 50}
    for key, val in defaults.items():
        data.setdefault(key, val)

    scraper_settings: dict[str, object] = {}
    if SCRAPER_SETTINGS_FILE.exists():
        try:
            scraper_settings = json.loads(SCRAPER_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            scraper_settings = {}

    data.setdefault("webhook_url", str(scraper_settings.get("webhook_url") or ""))
    data.setdefault("columns", scraper_settings.get("columns") or DEFAULT_COLUMNS)
    return data


def save_settings(settings: dict[str, object]) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _ensure_csv(value: object) -> str:
    """Convert a value to a comma-space-separated string.

    If the value is a list, join its elements with ', '.
    If it is already a string (or other scalar), return str(value) or ''.
    This prevents accidentally iterating over a string character-by-character.
    """
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value else ""


def _normalize_website(url: str) -> str:
    """Extract the canonical domain for comparison from any URL.

    Strips scheme, ``www.``, trailing slash, query parameters, and fragments
    so that all of the following produce ``example.com``:

        https://example.com
        http://www.example.com/
        https://www.example.com?ref=google
        https://www.example.com/#section

    An empty or non-URL string passes through as-is so callers don't need
    to guard against it.
    """
    url = url.strip().lower()
    if not url:
        return url
    if url.startswith(("http://", "https://")):
        url = url.split("://", 1)[1]
    if url.startswith("www."):
        url = url[4:]
    # Strip query string and fragment
    if "?" in url:
        url = url.split("?")[0]
    if "#" in url:
        url = url.split("#")[0]
    return url.rstrip("/")


def result_to_field_values(
    maps_business_name: str,
    maps_url: str,
    website: str,
    search_location: str,
    scraped: dict[str, object] | None,
    error: str = "",
    rating: str = "",
    review_count: str = "",
) -> dict[str, str]:
    scraped = scraped or {}
    socials = {
        "linkedin": str(scraped.get("linkedin") or ""),
        "facebook": str(scraped.get("facebook") or ""),
        "instagram": str(scraped.get("instagram") or ""),
        "twitter": str(scraped.get("twitter") or ""),
        "youtube": str(scraped.get("youtube") or ""),
        "tiktok": str(scraped.get("tiktok") or ""),
    }
    all_socials = ", ".join(value for value in socials.values() if value)
    return {
        "maps_business_name": maps_business_name,
        "source_url": website,
        "rating": rating or "N/A",
        "review_count": review_count or "N/A",
        "company_name": str(scraped.get("company_name") or ""),
        "email": _ensure_csv(scraped.get("email")),
        "phone": _ensure_csv(scraped.get("phone")),
        "location": search_location,
        "services": _ensure_csv(scraped.get("services")),
        "linkedin": socials["linkedin"],
        "facebook": socials["facebook"],
        "instagram": socials["instagram"],
        "twitter": socials["twitter"],
        "youtube": socials["youtube"],
        "tiktok": socials["tiktok"],
        "all_socials": all_socials or str(scraped.get("all_socials") or ""),
        "timestamp": str(scraped.get("timestamp") or ""),
        "maps_url": maps_url,
        "error": error,
    }


def build_sheet_payload(columns: list[dict[str, str]], field_values: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    headers: list[str] = []
    payload_values: dict[str, str] = {}
    for column in columns:
        header = str(column.get("header") or "").strip()
        field = str(column.get("field") or "").strip()
        if not header or not field:
            continue
        headers.append(header)
        payload_values[header] = field_values.get(field, "")
    return headers, payload_values


class MapsBatchLeadApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("Google Maps Lead Collector")
        self.geometry("960x800")
        self.minsize(860, 700)

        self.settings = load_settings()
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_requested = threading.Event()
        self.stop_scraping = threading.Event()
        self.worker: threading.Thread | None = None
        self.columns_list = list(self.settings.get("columns") or DEFAULT_COLUMNS)

        self.query_var = tk.StringVar(value=str(self.settings.get("query") or "marketing agency"))
        self.location_queue: list[str] = list(self.settings.get("location_queue") or self.settings.get("location") and [str(self.settings.get("location"))] or [])
        self.location_entry_var = tk.StringVar()
        self._batch_location_idx = -1
        self.max_results_var = tk.StringVar(value=str(self.settings.get("max_results") or 50))
        self.webhook_var = tk.StringVar(value=str(self.settings.get("webhook_url") or ""))
        self.credentials_path_var = tk.StringVar(value=str(self.settings.get("credentials_path") or ""))
        self.spreadsheet_id_var = tk.StringVar(value=str(self.settings.get("spreadsheet_id") or ""))
        self.col_header_var = tk.StringVar()
        self.headless_var = tk.BooleanVar(value=False)
        self.keep_awake_var = tk.BooleanVar(value=False)
        self.include_summary_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready.")
        self.progress_var = tk.DoubleVar(value=0)

        self.build_ui()
        self.poll_worker_queue()

    def build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f8fafc")
        style.configure("TLabel", background="#f8fafc", foreground="#0f172a", font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#f8fafc", foreground="#1f4e79", font=("Segoe UI", 16, "bold"))
        style.configure("Muted.TLabel", background="#f8fafc", foreground="#64748b", font=("Segoe UI", 9))
        style.configure("Primary.TButton", background="#1f4e79", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(12, 6))
        style.configure("Danger.TButton", background="#b91c1c", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(12, 6))

        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Google Maps Lead Collector", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            container,
            text="Collect business websites from Google Maps, run each website through your scraper, and send rows to Google Sheets.",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 14))

        # --- Scrollable content area (form, queue, sheet, column config) ---
        scroll_container = ttk.Frame(container)
        scroll_container.pack(fill=tk.BOTH, expand=True, pady=(0, 0))
        scroll_container.columnconfigure(0, weight=1)
        scroll_container.rowconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_container, highlightthickness=0, bg="#f8fafc")
        scrollbar = ttk.Scrollbar(scroll_container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        inner = ttk.Frame(canvas)

        def _on_inner_configure(_event: object = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_canvas_configure(event: object) -> None:
            try:
                canvas.itemconfig(1, width=event.width)
            except Exception:
                pass
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event: object) -> None:
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind("<MouseWheel>", _on_mousewheel)

        # ---- All content goes into `inner` now ----
        form = ttk.LabelFrame(inner, text=" Search Settings ", padding=12)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Business service:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=6)
        ttk.Entry(form, textvariable=self.query_var, font=("Segoe UI", 10)).grid(row=0, column=1, sticky=tk.EW, pady=6)

        ttk.Label(form, text="Max businesses:").grid(row=0, column=2, sticky=tk.W, padx=(16, 8), pady=6)
        ttk.Spinbox(form, from_=1, to=100, textvariable=self.max_results_var, width=10).grid(row=0, column=3, sticky=tk.W, pady=6)

        ttk.Checkbutton(form, text="Run browser in background", variable=self.headless_var).grid(
            row=1, column=0, columnspan=4, sticky=tk.W, pady=6
        )

        prefs_row = ttk.Frame(form)
        prefs_row.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))
        ttk.Checkbutton(
            prefs_row, text="Keep screen awake", variable=self.keep_awake_var
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            prefs_row,
            text="Include summary sheet",
            variable=self.include_summary_var,
        ).pack(side=tk.LEFT, padx=(16, 0))

        # --- Location Queue ---
        queue_frame = ttk.LabelFrame(inner, text=" Location Queue ", padding=12)
        queue_frame.pack(fill=tk.X, pady=(12, 0))

        input_row = ttk.Frame(queue_frame)
        input_row.pack(fill=tk.X)
        ttk.Label(input_row, text="Enter location:").pack(side=tk.LEFT)
        self.location_entry = ttk.Entry(input_row, textvariable=self.location_entry_var, width=40, font=("Segoe UI", 10))
        self.location_entry.pack(side=tk.LEFT, padx=(4, 8), fill=tk.X, expand=True)
        self.location_entry.bind("<Return>", lambda _e: self.add_location())
        ttk.Button(input_row, text="Add", command=self.add_location).pack(side=tk.LEFT)

        listbox_row = ttk.Frame(queue_frame)
        listbox_row.pack(fill=tk.X, pady=(6, 0))

        self.queue_listbox = tk.Listbox(listbox_row, height=3, font=("Segoe UI", 10))
        self.queue_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        q_scroll = ttk.Scrollbar(listbox_row, orient=tk.VERTICAL, command=self.queue_listbox.yview)
        q_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.queue_listbox.configure(yscrollcommand=q_scroll.set)

        q_btn_row = ttk.Frame(queue_frame)
        q_btn_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(q_btn_row, text="Remove Selected", command=self.remove_location).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(q_btn_row, text="Clear Queue", command=self.clear_location_queue).pack(side=tk.LEFT)
        self.refresh_queue_display()

        # --- Google Sheets Export ---
        sheet_frame = ttk.LabelFrame(inner, text=" Google Sheets Export ", padding=12)
        sheet_frame.pack(fill=tk.X, pady=(12, 0))
        sheet_frame.columnconfigure(0, weight=1)
        ttk.Label(sheet_frame, text="Google Sheet Web App URL:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(sheet_frame, textvariable=self.webhook_var, font=("Segoe UI", 10)).grid(
            row=1, column=0, sticky=tk.EW, pady=(4, 0)
        )
        self.webhook_var.trace_add("write", lambda *_args: self.save_current_settings())

        # --- Summary tab credentials (optional) ---
        cred_row = ttk.Frame(sheet_frame)
        cred_row.grid(row=2, column=0, sticky=tk.EW, pady=(10, 0))
        cred_row.columnconfigure(1, weight=1)

        ttk.Label(cred_row, text="Service Account JSON:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        cred_entry = ttk.Entry(cred_row, textvariable=self.credentials_path_var, font=("Segoe UI", 10))
        cred_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 6))
        ttk.Button(cred_row, text="Browse…", command=self._browse_credentials).grid(row=0, column=2)

        ttk.Label(cred_row, text="Spreadsheet ID:").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(4, 0))
        sid_entry = ttk.Entry(cred_row, textvariable=self.spreadsheet_id_var, font=("Segoe UI", 10))
        sid_entry.grid(row=1, column=1, sticky=tk.EW, padx=(0, 6), pady=(4, 0))

        self.credentials_path_var.trace_add("write", lambda *_args: self.save_current_settings())
        self.spreadsheet_id_var.trace_add("write", lambda *_args: self.save_current_settings())

        # --- Column Configuration ---
        mapper_frame = ttk.LabelFrame(inner, text=" Google Sheets Column Configuration ", padding=10)
        mapper_frame.pack(fill=tk.X, pady=(12, 0))

        tree_frame = ttk.Frame(mapper_frame)
        tree_frame.pack(fill=tk.X)

        self.columns_tree = ttk.Treeview(tree_frame, columns=("header", "field", "required"), show="headings", height=5)
        self.columns_tree.heading("header", text="Column Header")
        self.columns_tree.heading("field", text="Maps To Scraped Field")
        self.columns_tree.heading("required", text="✓ Req")
        self.columns_tree.column("header", width=240, anchor=tk.W)
        self.columns_tree.column("field", width=240, anchor=tk.W)
        self.columns_tree.column("required", width=65, anchor=tk.CENTER)
        self.columns_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.columns_tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.columns_tree.bind("<Button-1>", self.on_tree_click)

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.columns_tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.columns_tree.configure(yscrollcommand=tree_scroll.set)

        editor_frame = ttk.Frame(mapper_frame)
        editor_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(editor_frame, text="Header:").pack(side=tk.LEFT)
        header_entry = ttk.Entry(editor_frame, textvariable=self.col_header_var)
        header_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 10))

        ttk.Label(editor_frame, text="Maps To:").pack(side=tk.LEFT)
        self.col_field_combobox = ttk.Combobox(
            editor_frame,
            values=list(FIELD_OPTIONS.values()),
            state="readonly",
            width=26,
        )
        self.col_field_combobox.pack(side=tk.LEFT, padx=(4, 6))

        self.col_required_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(editor_frame, text="Required", variable=self.col_required_var).pack(
            side=tk.LEFT, padx=(0, 10)
        )

        self.col_field_combobox.set(FIELD_OPTIONS["maps_business_name"])

        ttk.Button(editor_frame, text="Add/Update", command=self.add_or_update_column).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(editor_frame, text="Remove", command=self.remove_column).pack(side=tk.LEFT, padx=4)
        ttk.Button(editor_frame, text="Up", command=lambda: self.move_column(-1)).pack(side=tk.RIGHT, padx=2)
        ttk.Button(editor_frame, text="Down", command=lambda: self.move_column(1)).pack(side=tk.RIGHT, padx=2)
        self.refresh_columns_tree()

        action_row = ttk.Frame(container)
        action_row.pack(fill=tk.X, pady=(12, 8))
        self.start_button = ttk.Button(action_row, text="Start Batch", style="Primary.TButton", command=self.start_batch)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(action_row, text="Stop After Current Lead", style="Danger.TButton", command=self.stop_batch)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_button.configure(state=tk.DISABLED)
        self.stop_scrape_button = ttk.Button(
            action_row, text="■ Stop Scraping", style="Danger.TButton",
            command=self.stop_scraping_cmd,
        )
        self.stop_scrape_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_scrape_button.configure(state=tk.DISABLED)

        self.progress = ttk.Progressbar(container, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 8))

        log_frame = ttk.LabelFrame(container, text=" Progress Log ", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_box = tk.Text(log_frame, height=18, wrap=tk.WORD, font=("Consolas", 9), bg="#ffffff", fg="#111827")
        self.log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_box.configure(yscrollcommand=scrollbar.set)

        ttk.Label(container, textvariable=self.status_var, style="Muted.TLabel").pack(anchor=tk.W, pady=(8, 0))

    def save_current_settings(self) -> None:
        save_settings(
            {
                "query": self.query_var.get().strip(),
                "location_queue": self.location_queue,
                "max_results": self.max_results_var.get().strip(),
                "webhook_url": self.webhook_var.get().strip(),
                "credentials_path": self.credentials_path_var.get().strip(),
                "spreadsheet_id": self.spreadsheet_id_var.get().strip(),
                "columns": self.columns_list,
            }
        )

    def _browse_credentials(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Service Account JSON Key",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.credentials_path_var.set(path)
            self.save_current_settings()

    # ---- Location queue management ----

    def add_location(self) -> None:
        location = self.location_entry_var.get().strip()
        if not location:
            messagebox.showwarning("Empty", "Enter a location name first.")
            return
        self.location_queue.append(location)
        self.location_entry_var.set("")
        self.location_entry.focus_set()
        self.refresh_queue_display()
        self.save_current_settings()

    def remove_location(self) -> None:
        selected = self.queue_listbox.curselection()
        if not selected:
            messagebox.showwarning("Selection Needed", "Select a location to remove.")
            return
        idx = selected[0]
        removed = self.location_queue.pop(idx)
        self.status_var.set(f"Removed '{removed}' from queue.")
        self.refresh_queue_display()
        self.save_current_settings()

    def clear_location_queue(self) -> None:
        if not self.location_queue:
            return
        if messagebox.askyesno("Clear Queue", "Remove all locations from the queue?"):
            self.location_queue.clear()
            self.refresh_queue_display()
            self.save_current_settings()
            self.status_var.set("Queue cleared.")

    def refresh_queue_display(self) -> None:
        self.queue_listbox.delete(0, tk.END)
        for idx, loc in enumerate(self.location_queue):
            if self._batch_location_idx >= 0:
                if idx < self._batch_location_idx:
                    prefix = "✅ "
                elif idx == self._batch_location_idx:
                    prefix = "🔄 "
                else:
                    prefix = "⏳ "
            else:
                prefix = ""
            self.queue_listbox.insert(tk.END, f"{prefix}{loc}")

    def refresh_columns_tree(self) -> None:
        for item in self.columns_tree.get_children():
            self.columns_tree.delete(item)

        for column in self.columns_list:
            field_label = FIELD_OPTIONS.get(str(column.get("field") or ""), str(column.get("field") or ""))
            required = column.get("required", False)
            required_display = "✅" if required else "❌"
            self.columns_tree.insert(
                "", tk.END, values=(column.get("header", ""), field_label, required_display)
            )

    def on_tree_select(self, _event: tk.Event) -> None:
        selected = self.columns_tree.selection()
        if not selected:
            return

        values = self.columns_tree.item(selected[0], "values")
        if values:
            self.col_header_var.set(str(values[0]))
            self.col_field_combobox.set(str(values[1]))
            # Restore required checkbox from the actual column config
            index = self.columns_tree.index(selected[0])
            if 0 <= index < len(self.columns_list):
                self.col_required_var.set(
                    bool(self.columns_list[index].get("required", False))
                )

    def on_tree_click(self, event: tk.Event) -> None:
        """Toggle the 'Required' state when clicking the '✓ Req' column cell."""
        col = self.columns_tree.identify_column(event.x)
        if col != "#3":  # only the "Required" column
            return

        row_id = self.columns_tree.identify_row(event.y)
        if not row_id:
            return

        index = self.columns_tree.index(row_id)
        if 0 <= index < len(self.columns_list):
            current = bool(self.columns_list[index].get("required", False))
            self.columns_list[index]["required"] = not current
            self.col_required_var.set(not current)
            self.refresh_columns_tree()
            # Re-select the row at the same index (ids are fresh after refresh)
            new_id = self.columns_tree.get_children()[index]
            self.columns_tree.selection_set(new_id)
            self.save_current_settings()
            return "break"

    def add_or_update_column(self) -> None:
        header = self.col_header_var.get().strip()
        field_label = self.col_field_combobox.get().strip()
        field_code = REVERSE_FIELD_OPTIONS.get(field_label)

        if not header or not field_code:
            messagebox.showwarning("Validation Error", "Enter a header and choose which scraped field it maps to.")
            return

        required = self.col_required_var.get()
        column_entry = {"header": header, "field": field_code, "required": required}

        selected = self.columns_tree.selection()
        if selected:
            index = self.columns_tree.index(selected[0])
            self.columns_list[index] = column_entry
            self.status_var.set(f"Updated column {index + 1}.")
        else:
            if any(str(column.get("header") or "").lower() == header.lower() for column in self.columns_list):
                messagebox.showwarning("Duplicate Header", f"'{header}' already exists in the column list.")
                return
            self.columns_list.append(column_entry)
            self.status_var.set(f"Added column '{header}'.")

        self.refresh_columns_tree()
        self.columns_tree.selection_remove(self.columns_tree.selection())
        self.col_header_var.set("")
        self.save_current_settings()

    def remove_column(self) -> None:
        selected = self.columns_tree.selection()
        if not selected:
            messagebox.showwarning("Selection Needed", "Select a column to remove.")
            return

        index = self.columns_tree.index(selected[0])
        removed = self.columns_list.pop(index)
        self.status_var.set(f"Removed column '{removed.get('header', '')}'.")
        self.refresh_columns_tree()
        self.col_header_var.set("")
        self.save_current_settings()

    def move_column(self, direction: int) -> None:
        selected = self.columns_tree.selection()
        if not selected:
            return

        index = self.columns_tree.index(selected[0])
        new_index = index + direction
        if not 0 <= new_index < len(self.columns_list):
            return

        self.columns_list[index], self.columns_list[new_index] = self.columns_list[new_index], self.columns_list[index]
        self.refresh_columns_tree()
        item = self.columns_tree.get_children()[new_index]
        self.columns_tree.selection_set(item)
        self.save_current_settings()

    def start_batch(self) -> None:
        query = self.query_var.get().strip()
        webhook_url = self.webhook_var.get().strip()

        try:
            max_results = int(self.max_results_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid range", "Max businesses must be a number.")
            return

        if not query:
            messagebox.showwarning("Missing search", "Enter a business service to search for.")
            return

        if not self.location_queue:
            messagebox.showwarning("Missing locations", "Add at least one location to the queue.")
            return

        if not webhook_url:
            messagebox.showwarning("Missing webhook", "Paste your Google Sheet Web App URL first.")
            return

        if max_results < 1 or max_results > 100:
            messagebox.showwarning("Invalid range", "Use a max businesses value between 1 and 100.")
            return

        if not self.columns_list:
            messagebox.showwarning("Missing columns", "Add at least one Google Sheet column mapping.")
            return

        self.save_current_settings()

        self.log_box.delete("1.0", tk.END)
        self.progress_var.set(0)
        self.stop_requested.clear()
        self.stop_scraping.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.stop_scrape_button.configure(state=tk.NORMAL)
        self.status_var.set("Starting Google Maps collection...")

        self.worker = threading.Thread(
            target=self.run_batch,
            args=(query, list(self.location_queue), max_results, webhook_url, self.headless_var.get(), list(self.columns_list), self.include_summary_var.get(), self.credentials_path_var.get().strip(), self.spreadsheet_id_var.get().strip()),
            daemon=True,
        )
        self.worker.start()

    def stop_batch(self) -> None:
        self.stop_requested.set()
        self.status_var.set("Stop requested. The app will finish the current lead first.")

    def stop_scraping_cmd(self) -> None:
        """Immediately stop: collection, scraping, export, and next locations."""
        self.stop_scraping.set()
        self.stop_scrape_button.configure(state=tk.DISABLED)
        self.status_var.set("■ Stop requested — stopping immediately...")

    def run_batch(
        self,
        query: str,
        locations: list[str],
        max_results: int,
        webhook_url: str,
        headless: bool,
        columns: list[dict[str, str]],
        include_summary: bool = False,
        credentials_path: str = "",
        spreadsheet_id: str = "",
    ) -> None:
        _awake = self.keep_awake_var.get()
        if _awake:
            _prevent_sleep()
        try:
            start_time = time.time()
            _test_run_id = f"ztest_{datetime.now():%Y%m%d_%H%M%S}"
            self.worker_queue.put(("log", f"[TEST_RUN] marker={_test_run_id}"))
            total_location_count = len(locations)
            all_qualified = 0
            all_total_raw = 0
            all_emails_found = 0
            all_linkedin_found = 0
            all_instagram_found = 0
            all_duplicates_removed = 0

            # --- Load processed cache (Accepted + Rejected) into dedup sets ---
            processed_names: set[str] = set()
            processed_websites: set[str] = set()
            scraped_cache: dict[str, dict[str, object]] = {}
            if credentials_path and spreadsheet_id:
                try:
                    raw_names, raw_websites = download_processed_cache(credentials_path, spreadsheet_id)
                    processed_names = raw_names
                    # Normalize websites so comparisons use canonical domains
                    processed_websites = {_normalize_website(w) for w in raw_websites}
                    self.worker_queue.put((
                        "log",
                        f"Cache loaded: {len(processed_names)} names, {len(processed_websites)} websites "
                        f"(Accepted + Rejected)."
                    ))
                except Exception as exc:
                    _cause = getattr(exc, '__cause__', None) or getattr(exc, '__context__', None)
                    _detail = f": {_cause}" if _cause else ""
                    self.worker_queue.put(("log", f"Cache load failed: {exc}{_detail}"))
            else:
                self.worker_queue.put(("log",
                    "No credentials_path / spreadsheet_id configured — duplicate prevention disabled. "
                    "Set Service Account JSON and Spreadsheet ID to enable caching."))

            # --- Helper: append a rejected lead to the Rejected sheet (non-blocking) ---
            def _reject(name: str, site: str, loc: str, reason: str,
                        field_values: dict[str, str] | None = None,
                        reject_columns: list[dict[str, str]] | None = None) -> None:
                """Append a lead to the Rejected sheet; logs errors."""
                if not credentials_path or not spreadsheet_id:
                    return
                try:
                    append_rejected(credentials_path, spreadsheet_id, name, site, loc, reason,
                                    columns=reject_columns, field_values=field_values)
                except Exception as _exc:
                    _cause = getattr(_exc, '__cause__', None) or getattr(_exc, '__context__', None)
                    _detail = f": {_cause}" if _cause else ""
                    self.worker_queue.put(("log", f"Rejected write error: {_exc}{_detail}"))

            for loc_idx, location in enumerate(locations):
                if self.stop_requested.is_set() or self.stop_scraping.is_set():
                    self.worker_queue.put(("done", f"Stopped after location {loc_idx} of {total_location_count}."))
                    return

                self.worker_queue.put(("set_batch_location", loc_idx))
                self.worker_queue.put(("log", f"{'='*50}"))
                self.worker_queue.put(("log", f"Location {loc_idx + 1} of {total_location_count}: {location}"))
                self.worker_queue.put(("log", f"{'='*50}"))

                # --- Collect websites for THIS location ---
                try:
                    websites = collect_websites_from_google_maps(
                        query=query,
                        location=location,
                        max_results=max_results,
                        headed=not headless,
                        slow_mo_ms=80,
                        stop_event=self.stop_scraping,
                        processed_names=processed_names if processed_names else None,
                        processed_websites=processed_websites if processed_websites else None,
                    )
                except RuntimeError as error:
                    self.worker_queue.put(("log", f"  ERROR collecting websites: {error}"))
                    continue

                if self.stop_scraping.is_set():
                    self.worker_queue.put(("done", "Stop requested — collection interrupted."))
                    return

                if not websites:
                    self.worker_queue.put(("log", f"  No websites found for {location}. Skipping."))
                    continue

                self.worker_queue.put(("log", f"Collected {len(websites)} websites from {location}."))

                # --- Check processed cache BEFORE expensive scraping ---
                pre_scrape_skipped = 0
                filtered: list[dict[str, str]] = []
                for w in websites:
                    raw_name = w.get("business_name") or ""
                    raw_site = w.get("website") or ""
                    biz_name = raw_name.strip().lower()
                    site_url = _normalize_website(raw_site)
                    name_matched = bool(biz_name and biz_name in processed_names)
                    website_matched = bool(site_url and site_url in processed_websites)

                    if name_matched or website_matched:
                        pre_scrape_skipped += 1
                    else:
                        filtered.append(w)
                websites = filtered
                if pre_scrape_skipped:
                    self.worker_queue.put(("log", f"  Skipped {pre_scrape_skipped} already-processed leads before scraping."))

                if not websites:
                    self.worker_queue.put(("log", f"  No new websites to scrape for {location}. Skipping."))
                    continue

                # --- Check in-memory scrape cache (same website already scraped in an earlier city) ---
                cache_hits: list[dict[str, str]] = []
                needs_scrape: list[dict[str, str]] = []
                for w in websites:
                    site_url = _normalize_website(w.get("website") or "")
                    cached = scraped_cache.get(site_url)
                    if cached:
                        enriched_lead = {**w, **cached}
                        cache_hits.append(enriched_lead)
                    else:
                        needs_scrape.append(w)

                if cache_hits:
                    self.worker_queue.put(("log", f"  Reusing {len(cache_hits)} cached scrape results."))

                # --- Scrape uncached websites ---
                freshly_scraped: list[dict[str, str]] = []
                if needs_scrape:
                    self.worker_queue.put(("log", f"Scraping {len(needs_scrape)} websites..."))
                    freshly_scraped = scrape_websites(needs_scrape, max_workers=10, stop_event=self.stop_scraping)

                    if self.stop_scraping.is_set():
                        self.worker_queue.put(("done", "Stop requested — website scraping interrupted."))
                        return

                    # Store fresh scrapes in the cross-city cache
                    for result in freshly_scraped:
                        site_url = _normalize_website(result.get("website") or "")
                        if site_url:
                            scraped_cache[site_url] = {
                                "company_name": result.get("company_name"),
                                "email": result.get("email"),
                                "phone": result.get("phone"),
                                "location": result.get("location"),
                                "services": result.get("services"),
                                "instagram": result.get("instagram"),
                                "facebook": result.get("facebook"),
                                "linkedin": result.get("linkedin"),
                                "twitter": result.get("twitter"),
                                "youtube": result.get("youtube"),
                                "tiktok": result.get("tiktok"),
                            }

                # --- Combine cache hits with freshly scraped ---
                enriched = cache_hits + freshly_scraped

                # --- Deduplicate (within this location, with normalized websites) ---
                seen_names: set[str] = set()
                seen_websites: set[str] = set()
                loc_raw = len(enriched)
                loc_duplicates = 0
                deduped: list[dict[str, str]] = []
                for lead in enriched:
                    name = str(lead.get("business_name") or "").strip().lower()
                    site = _normalize_website(lead.get("website") or "")
                    if (name and name in seen_names) or (site and site in seen_websites):
                        loc_duplicates += 1
                        continue
                    if name:
                        seen_names.add(name)
                    if site:
                        seen_websites.add(site)
                    deduped.append(lead)

                # Accumulate for summary
                all_total_raw += loc_raw
                all_duplicates_removed += loc_duplicates

                # --- Process and send each lead for THIS location ---
                loc_qualified = 0
                loc_emails_found = 0
                loc_linkedin_found = 0
                loc_instagram_found = 0
                loc_webhook_ok = 0

                for index, lead in enumerate(deduped, start=1):
                    if self.stop_scraping.is_set():
                        self.worker_queue.put(("done", "■ Stop requested — export interrupted."))
                        return

                    if self.stop_requested.is_set():
                        self.worker_queue.put(("done", f"Stopped after {index - 1} leads in {location}."))
                        return

                    business_name = str(lead.get("business_name") or "")
                    maps_url = str(lead.get("maps_url") or "")
                    website = str(lead.get("website") or "")
                    self.worker_queue.put(("log", f"[{loc_idx + 1}.{index}] {business_name or website} ({location})"))

                    scraped = {
                        "company_name": lead.get("company_name"),
                        "email": lead.get("email"),
                        "phone": lead.get("phone"),
                        "location": lead.get("location"),
                        "services": lead.get("services"),
                        "instagram": lead.get("instagram"),
                        "facebook": lead.get("facebook"),
                        "linkedin": lead.get("linkedin"),
                        "twitter": lead.get("twitter"),
                        "youtube": lead.get("youtube"),
                        "tiktok": lead.get("tiktok"),
                    }
                    error = lead.get("error", "") or ""

                    # Use the lead's search_location (preserved from collection)
                    lead_location = lead.get("search_location") or location

                    field_values = result_to_field_values(
                        maps_business_name=business_name,
                        maps_url=maps_url,
                        website=website,
                        search_location=lead_location,
                        scraped=scraped,
                        error=error,
                        rating=str(lead.get("rating") or ""),
                        review_count=str(lead.get("review_count") or ""),
                    )

                    # --- Count raw scraped fields ---
                    raw_email = str(lead.get("email") or "").strip()
                    if raw_email and raw_email.lower() not in ("n/a", "contact form only"):
                        loc_emails_found += 1
                    if str(lead.get("linkedin") or "").strip():
                        loc_linkedin_found += 1
                    if str(lead.get("instagram") or "").strip():
                        loc_instagram_found += 1

                    # --- Check required fields ---
                    missing_required = []
                    for col in columns:
                        if col.get("required", False):
                            field_code = str(col.get("field") or "").strip()
                            raw_val = scraped.get(field_code)
                            if raw_val is None:
                                missing_required.append(field_code)
                            elif isinstance(raw_val, list) and not raw_val:
                                missing_required.append(field_code)
                            elif isinstance(raw_val, str) and not raw_val.strip():
                                missing_required.append(field_code)
                            elif isinstance(raw_val, str) and raw_val.strip().lower() in ("n/a", "contact form only"):
                                missing_required.append(field_code)

                    # Compute normalized values upfront for cache updates
                    norm_name = business_name.strip().lower()
                    norm_website = _normalize_website(website)

                    # --- Append to Rejected if missing required fields ---
                    if missing_required:
                        msg = f"Missing Required Field(s): {', '.join(missing_required)}"
                        self.worker_queue.put(("log", f"  SKIPPED ({business_name or website}) — {msg}"))
                        _reject(business_name, website, lead_location, msg, field_values=field_values, reject_columns=columns)
                        # Update processed cache so this business is not retried
                        if norm_name:
                            processed_names.add(norm_name)
                        if norm_website:
                            processed_websites.add(norm_website)
                        continue

                    loc_qualified += 1

                    headers, values = build_sheet_payload(columns, field_values)

                    try:
                        _resp_data = send_result_to_sheet(webhook_url, headers, values)

                        loc_webhook_ok += 1
                        if norm_name:
                            processed_names.add(norm_name)
                        if norm_website:
                            processed_websites.add(norm_website)
                        self.worker_queue.put(("log", f"✓ Sent to Google Sheets ({location})"))
                    except requests.HTTPError as exc:
                        _reject(business_name, website, lead_location, f"Export Error: {exc}", field_values=field_values, reject_columns=columns)
                        if norm_name:
                            processed_names.add(norm_name)
                        if norm_website:
                            processed_websites.add(norm_website)
                    except Exception as exc:
                        _reject(business_name, website, lead_location, f"Export Error: {exc}", field_values=field_values, reject_columns=columns)
                        if norm_name:
                            processed_names.add(norm_name)
                        if norm_website:
                            processed_websites.add(norm_website)

                # Accumulate location counters into totals
                all_qualified += loc_qualified
                all_emails_found += loc_emails_found
                all_linkedin_found += loc_linkedin_found
                all_instagram_found += loc_instagram_found

                # Update UI progress across all locations
                loc_progress = ((loc_idx + 1) / total_location_count) * 100
                self.worker_queue.put(("progress", loc_progress))
                self.worker_queue.put(("log", f"===== LOCATION COMPLETE ====="))
                self.worker_queue.put(("log", f"  Location: {location}"))
                self.worker_queue.put(("log", f"  Maps businesses collected: {len(websites)}"))
                self.worker_queue.put(("log", f"  Websites scraped: {len(enriched)}"))
                self.worker_queue.put(("log", f"  After dedup: {len(deduped)}"))
                self.worker_queue.put(("log", f"  After required-field filter: {loc_qualified}"))
                self.worker_queue.put(("log", f"  Exported to Google Sheets: {loc_webhook_ok}"))
                self.worker_queue.put(("log", f"  Successful webhook POSTs: {loc_webhook_ok}"))
                self.worker_queue.put(("log", f"  Duplicates removed: {loc_duplicates}"))
                self.worker_queue.put(("log", f"==============================="))

            # Reset batch location indicator
            self.worker_queue.put(("set_batch_location", -1))

            if all_qualified == 0:
                self.worker_queue.put(("done", "No qualified leads found across any location."))
                return

            # --- Summary (optional) ---
            if include_summary:
                elapsed = time.time() - start_time
                minutes = int(elapsed // 60)
                seconds = int(elapsed % 60)
                time_str = f"{minutes} min {seconds} sec" if minutes else f"{seconds} seconds"

                if credentials_path and spreadsheet_id:
                    try:
                        summary_data = [
                            {"Label": "Companies Found", "Value": str(all_total_raw)},
                            {"Label": "Qualified Leads", "Value": str(all_qualified)},
                            {"Label": "Emails Found", "Value": str(all_emails_found)},
                            {"Label": "LinkedIn Profiles", "Value": str(all_linkedin_found)},
                            {"Label": "Instagram Profiles", "Value": str(all_instagram_found)},
                            {"Label": "Duplicates Removed", "Value": str(all_duplicates_removed)},
                            {"Label": "Time Generated", "Value": time_str},
                            {"Label": "Date Run", "Value": datetime.now().strftime("%Y-%m-%d %H:%M")},
                        ]
                        write_summary_sheet(credentials_path, spreadsheet_id, summary_data)
                        self.worker_queue.put(("log", "Summary tab written to Google Sheets."))
                    except Exception as exc:
                        self.worker_queue.put(("log", f"Summary tab error: {exc}"))

            self.worker_queue.put(("done", f"Completed {all_qualified} leads across {total_location_count} locations."))
        finally:
            if _awake:
                _restore_sleep()

    def poll_worker_queue(self) -> None:
        try:
            while True:
                message_type, payload = self.worker_queue.get_nowait()
                if message_type == "log":
                    self.write_log(str(payload))
                elif message_type == "progress":
                    self.progress_var.set(float(payload))
                elif message_type == "set_batch_location":
                    self._batch_location_idx = int(payload)  # type: ignore[arg-type]
                    self.refresh_queue_display()
                elif message_type == "done":
                    self.finish_batch(str(payload))
        except queue.Empty:
            pass

        self.after(100, self.poll_worker_queue)

    def write_log(self, message: str) -> None:
        self.log_box.insert(tk.END, f"{message}\n")
        self.log_box.see(tk.END)
        self.status_var.set(message)

    def finish_batch(self, message: str) -> None:
        self._batch_location_idx = -1
        self.refresh_queue_display()
        self.write_log(message)
        self.stop_requested.clear()
        self.stop_scraping.clear()
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.stop_scrape_button.configure(state=tk.DISABLED)
        self.status_var.set(message)


if __name__ == "__main__":
    MapsBatchLeadApp().mainloop()
