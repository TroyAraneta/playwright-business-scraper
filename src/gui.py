from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
from urllib.error import HTTPError, URLError

from app import scrape_company
from sheets import send_result_to_sheet

try:
    from maps_website_collector import collect_websites_from_google_maps
except ImportError:
    collect_websites_from_google_maps = None

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "logs" / "scraper_settings.json"

FIELD_OPTIONS = {
    "maps_business_name": "Google Maps Business Name",
    "company_name": "Company Name",
    "source_url": "Website URL",
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

REVERSE_FIELD_OPTIONS = {v: k for k, v in FIELD_OPTIONS.items()}


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                # Verify schema
                if "webhook_url" in data and "columns" in data:
                    return data
        except Exception:
            pass
    # Default initial settings
    return {
        "webhook_url": "",
        "columns": [
            {"header": "Company Name", "field": "company_name"},
            {"header": "Website", "field": "source_url"},
            {"header": "Company Email", "field": "email"},
            {"header": "Location", "field": "location"},
            {"header": "Services", "field": "services"},
        ],
    }


def save_settings(webhook_url: str, columns: list[dict[str, str]]) -> None:
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"webhook_url": webhook_url, "columns": columns}, f, indent=2)
    except Exception as e:
        print(f"Error saving settings: {e}")


class CompanyScraperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("Company Website Scraper & Sheets Exporter")
        self.geometry("1100x760")
        self.minsize(960, 680)

        # Queues for background thread communications
        self.result_queue: queue.Queue[dict[str, object]] = queue.Queue()
        self.sheet_queue: queue.Queue[tuple[bool, str]] = queue.Queue()
        self.batch_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_batch_requested = threading.Event()

        # Load settings
        self.settings = load_settings()
        self.columns_list = self.settings["columns"]

        # Variables for inputs
        self.url_var = tk.StringVar()
        self.sheet_webhook_var = tk.StringVar(value=self.settings["webhook_url"])
        self.homepage_only_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Enter a website URL to begin.")
        self.maps_query_var = tk.StringVar(value="marketing agency")
        self.maps_location_var = tk.StringVar(value="Austin Texas")
        self.maps_max_results_var = tk.StringVar(value="50")
        self.maps_headless_var = tk.BooleanVar(value=False)

        # Variables for editable results
        self.company_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.phone_var = tk.StringVar()
        self.location_var = tk.StringVar()
        self.services_var = tk.StringVar()
        self.linkedin_var = tk.StringVar()
        self.facebook_var = tk.StringVar()
        self.instagram_var = tk.StringVar()
        self.twitter_var = tk.StringVar()
        self.youtube_var = tk.StringVar()
        self.tiktok_var = tk.StringVar()
        self.all_socials_var = tk.StringVar()
        self.timestamp_var = tk.StringVar()

        # Keep a reference to currently scraped details for mappings
        self.last_scraped_raw: dict[str, object] = {}

        self.configure(bg="#f8fafc")  # Slate-50 background
        self.build_ui()
        self.poll_results()
        self.poll_sheet_results()
        self.poll_batch_results()

    def build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        # Color System Config
        style.configure("TFrame", background="#f8fafc")
        style.configure("Panel.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("TLabel", background="#f8fafc", foreground="#0f172a", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#0f172a")
        style.configure("Header.TLabel", background="#f8fafc", foreground="#4f46e5", font=("Segoe UI", 14, "bold"))
        style.configure("PanelHeader.TLabel", background="#ffffff", foreground="#4f46e5", font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", background="#f8fafc", foreground="#64748b", font=("Segoe UI", 9))
        style.configure("PanelMuted.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 9))
        
        # Inputs & Entries
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#cbd5e1", lightcolor="#cbd5e1", darkcolor="#cbd5e1")
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor="#cbd5e1")

        # Buttons
        style.configure("TButton", background="#f1f5f9", foreground="#0f172a", borderwidth=1, bordercolor="#cbd5e1", font=("Segoe UI", 9), padding=(8, 4))
        style.map("TButton", background=[("active", "#e2e8f0")])
        
        style.configure("Primary.TButton", background="#4f46e5", foreground="#ffffff", borderwidth=0, font=("Segoe UI", 10, "bold"), padding=(12, 6))
        style.map("Primary.TButton", background=[("active", "#4338ca"), ("disabled", "#94a3b8")], foreground=[("disabled", "#cbd5e1")])
        
        style.configure("Accent.TButton", background="#10b981", foreground="#ffffff", borderwidth=0, font=("Segoe UI", 10, "bold"), padding=(12, 6))
        style.map("Accent.TButton", background=[("active", "#059669"), ("disabled", "#94a3b8")], foreground=[("disabled", "#cbd5e1")])

        # Treeview styling
        style.configure("Treeview", background="#ffffff", foreground="#0f172a", fieldbackground="#ffffff", bordercolor="#cbd5e1", rowheight=24)
        style.configure("Treeview.Heading", background="#e2e8f0", foreground="#0f172a", font=("Segoe UI", 9, "bold"))

        style.configure(
            "Status.TLabel",
            background="#f8fafc",
            foreground="#0f172a",
            font=("Segoe UI", 9),
            padding=(6, 4),
            relief="sunken",
        )

        # Main Layout: Top Header + Main content area
        main_container = ttk.Frame(self, padding=16)
        main_container.pack(fill=tk.BOTH, expand=True)

        # Header Title
        title_frame = ttk.Frame(main_container)
        title_frame.pack(fill=tk.X, pady=(0, 12))

        title = ttk.Label(title_frame, text="Company Scraper & Sheet Mapper", style="Header.TLabel")
        title.pack(anchor=tk.W)
        subtitle = ttk.Label(title_frame, text="Define columns, extract emails/socials/services, and export mapped columns to Google Sheets.", style="Muted.TLabel")
        subtitle.pack(anchor=tk.W, pady=(2, 0))

        # Main Pane splits into Left controls & Right results
        panes = ttk.Frame(main_container)
        panes.pack(fill=tk.BOTH, expand=True)

        left_pane = ttk.Frame(panes)
        left_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_pane = ttk.Frame(panes, style="Panel.TFrame", padding=14)
        right_pane.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))

        # ================= LEFT PANE: Crawler & Sheets Table Configuration =================
        
        # 1. Crawler Input Frame
        crawler_frame = ttk.Frame(left_pane)
        crawler_frame.pack(fill=tk.X, pady=(0, 10))
        
        url_label = ttk.Label(crawler_frame, text="Website URL to Scrape:", font=("Segoe UI", 9, "bold"))
        url_label.pack(anchor=tk.W, pady=(0, 4))
        
        url_row = ttk.Frame(crawler_frame)
        url_row.pack(fill=tk.X)
        
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var, font=("Segoe UI", 11))
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        self.url_entry.bind("<Return>", lambda _e: self.start_scrape())
        
        self.scrape_button = ttk.Button(url_row, text="Scrape Website", style="Primary.TButton", command=self.start_scrape)
        self.scrape_button.pack(side=tk.LEFT, padx=(8, 0))

        crawler_options = ttk.Frame(crawler_frame)
        crawler_options.pack(fill=tk.X, pady=(4, 0))
        homepage_only_cb = ttk.Checkbutton(crawler_options, text="Homepage only (skip following contact links)", variable=self.homepage_only_var)
        homepage_only_cb.pack(anchor=tk.W)

        # 1b. Google Maps batch workflow
        maps_frame = ttk.LabelFrame(left_pane, text=" Google Maps Batch Leads ", labelanchor=tk.N, padding=10)
        maps_frame.pack(fill=tk.X, pady=(0, 10))
        maps_frame.columnconfigure(1, weight=1)
        maps_frame.columnconfigure(3, weight=1)

        ttk.Label(maps_frame, text="Business service:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        ttk.Entry(maps_frame, textvariable=self.maps_query_var, font=("Segoe UI", 9)).grid(row=0, column=1, sticky=tk.EW, pady=4)

        ttk.Label(maps_frame, text="Location:").grid(row=0, column=2, sticky=tk.W, padx=(10, 6), pady=4)
        ttk.Entry(maps_frame, textvariable=self.maps_location_var, font=("Segoe UI", 9)).grid(row=0, column=3, sticky=tk.EW, pady=4)

        ttk.Label(maps_frame, text="Max businesses:").grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        ttk.Spinbox(maps_frame, from_=1, to=100, textvariable=self.maps_max_results_var, width=8).grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Checkbutton(maps_frame, text="Run browser in background", variable=self.maps_headless_var).grid(row=1, column=2, sticky=tk.W, padx=(10, 6), pady=4)

        maps_buttons = ttk.Frame(maps_frame)
        maps_buttons.grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=(6, 0))
        self.maps_batch_button = ttk.Button(maps_buttons, text="Start Batch", style="Accent.TButton", command=self.start_maps_batch)
        self.maps_batch_button.pack(side=tk.LEFT)
        self.stop_maps_batch_button = ttk.Button(maps_buttons, text="Stop After Current Lead", command=self.stop_maps_batch)
        self.stop_maps_batch_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_maps_batch_button.configure(state=tk.DISABLED)

        # 2. Table Column Mapper Frame
        mapper_frame = ttk.LabelFrame(left_pane, text=" Google Sheets Column Configuration ", labelanchor=tk.N, padding=10)
        mapper_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 10))

        # Treeview list of configured columns
        tree_frame = ttk.Frame(mapper_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.columns_tree = ttk.Treeview(tree_frame, columns=("header", "field", "required"), show="headings", height=8)
        self.columns_tree.heading("header", text="Column Header")
        self.columns_tree.heading("field", text="Maps To Scraped Field")
        self.columns_tree.heading("required", text="✓ Req")
        self.columns_tree.column("header", width=190, anchor=tk.W)
        self.columns_tree.column("field", width=170, anchor=tk.W)
        self.columns_tree.column("required", width=65, anchor=tk.CENTER)
        self.columns_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.columns_tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.columns_tree.configure(yscrollcommand=tree_scroll.set)
        self.columns_tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.columns_tree.bind("<Button-1>", self.on_tree_click)

        # Editor frame below list
        editor_frame = ttk.Frame(mapper_frame)
        editor_frame.pack(fill=tk.X, pady=(10, 0))

        # Inputs row
        inputs_row = ttk.Frame(editor_frame)
        inputs_row.pack(fill=tk.X)

        self.col_header_var = tk.StringVar()
        ttk.Label(inputs_row, text="Header:").pack(side=tk.LEFT)
        self.col_header_entry = ttk.Entry(inputs_row, textvariable=self.col_header_var, font=("Segoe UI", 9))
        self.col_header_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        ttk.Label(inputs_row, text="Maps To:").pack(side=tk.LEFT)
        self.col_field_combobox = ttk.Combobox(inputs_row, values=list(FIELD_OPTIONS.values()), state="readonly", width=18, font=("Segoe UI", 9))
        self.col_field_combobox.pack(side=tk.LEFT, padx=4)

        self.col_required_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inputs_row, text="Required", variable=self.col_required_var).pack(side=tk.LEFT, padx=(4, 0))

        self.col_field_combobox.set("Company Name")

        # Action Buttons row
        actions_row = ttk.Frame(editor_frame)
        actions_row.pack(fill=tk.X, pady=(6, 0))

        self.add_update_btn = ttk.Button(actions_row, text="Add/Update Column", command=self.add_or_update_column)
        self.add_update_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.remove_col_btn = ttk.Button(actions_row, text="Remove Column", command=self.remove_column)
        self.remove_col_btn.pack(side=tk.LEFT, padx=4)

        self.move_up_btn = ttk.Button(actions_row, text="▲ Up", command=lambda: self.move_column(-1))
        self.move_up_btn.pack(side=tk.RIGHT, padx=2)

        self.move_down_btn = ttk.Button(actions_row, text="▼ Down", command=lambda: self.move_column(1))
        self.move_down_btn.pack(side=tk.RIGHT, padx=2)

        # Render loaded columns into Treeview
        self.refresh_columns_tree()

        # ================= RIGHT PANE: SCRAPED RESULTS (EDITABLE) =================
        results_header = ttk.Frame(right_pane, style="Panel.TFrame")
        results_header.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(results_header, text="Scraped Results Preview & Manual Edits", style="PanelHeader.TLabel").pack(side=tk.LEFT)
        ttk.Label(results_header, text="Modify these values prior to sheet export if needed.", style="PanelMuted.TLabel").pack(side=tk.RIGHT)

        # Scrollable area for result entries
        results_canvas = tk.Canvas(
            right_pane, highlightthickness=0, bg="#ffffff"
        )
        results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        results_scrollbar = ttk.Scrollbar(
            right_pane, orient=tk.VERTICAL, command=results_canvas.yview
        )
        results_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        results_canvas.configure(yscrollcommand=results_scrollbar.set)

        results_inner = ttk.Frame(results_canvas, style="Panel.TFrame")

        def _on_results_inner_configure(_event: object = None) -> None:
            results_canvas.configure(scrollregion=results_canvas.bbox("all"))

        results_inner.bind("<Configure>", _on_results_inner_configure)

        results_canvas.create_window((0, 0), window=results_inner, anchor="nw")

        def _on_results_canvas_configure(event: object) -> None:
            try:
                results_canvas.itemconfig(1, width=event.width)
            except Exception:
                pass

        results_canvas.bind("<Configure>", _on_results_canvas_configure)

        def _on_results_mousewheel(event: object) -> None:
            try:
                results_canvas.yview_scroll(
                    int(-1 * (event.delta / 120)), "units"
                )
            except Exception:
                pass

        results_canvas.bind("<MouseWheel>", _on_results_mousewheel)

        # Layout fields inside the scrollable inner frame
        grid_frame = ttk.Frame(results_inner, style="Panel.TFrame")
        grid_frame.pack(fill=tk.BOTH, expand=True)
        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=1)

        # Left Column in Results Pane
        left_grid = ttk.Frame(grid_frame, style="Panel.TFrame")
        left_grid.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.add_edit_field(left_grid, "Company Name", self.company_var)
        self.add_edit_field(left_grid, "Company Email(s)", self.email_var)
        self.add_edit_field(left_grid, "Phone Number(s)", self.phone_var)
        self.add_edit_field(left_grid, "Location / Address", self.location_var)
        self.add_edit_field(left_grid, "Services Offered", self.services_var)

        # Right Column in Results Pane (Social Links)
        right_grid = ttk.Frame(grid_frame, style="Panel.TFrame")
        right_grid.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.add_edit_field(right_grid, "LinkedIn URL", self.linkedin_var)
        self.add_edit_field(right_grid, "Facebook URL", self.facebook_var)
        self.add_edit_field(right_grid, "Instagram URL", self.instagram_var)
        self.add_edit_field(right_grid, "Twitter/X URL", self.twitter_var)
        self.add_edit_field(right_grid, "YouTube URL", self.youtube_var)
        self.add_edit_field(right_grid, "TikTok URL", self.tiktok_var)

        # Muted Timestamp label
        ts_row = ttk.Frame(right_pane, style="Panel.TFrame")
        ts_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(ts_row, text="Last Scraped At:", style="PanelMuted.TLabel").pack(side=tk.LEFT)
        ttk.Label(ts_row, textvariable=self.timestamp_var, font=("Segoe UI", 9, "bold"), background="#ffffff", foreground="#0f172a").pack(side=tk.LEFT, padx=4)

        # Webhook & Send to sheet at the very bottom
        bottom_panel = ttk.Frame(main_container)
        bottom_panel.pack(fill=tk.X, pady=(12, 0))

        webhook_frame = ttk.Frame(bottom_panel)
        webhook_frame.pack(fill=tk.X)

        ttk.Label(webhook_frame, text="Google Sheet Webhook URL:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(0, 4))
        
        webhook_row = ttk.Frame(webhook_frame)
        webhook_row.pack(fill=tk.X)

        self.webhook_entry = ttk.Entry(webhook_row, textvariable=self.sheet_webhook_var, font=("Segoe UI", 10))
        self.webhook_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        self.webhook_entry.bind("<KeyRelease>", self.on_webhook_change)

        self.send_sheet_btn = ttk.Button(webhook_row, text="Send to Sheet", style="Accent.TButton", command=self.start_send_to_sheet)
        self.send_sheet_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Status Bar
        self.status_label = ttk.Label(bottom_panel, textvariable=self.status_var, style="Status.TLabel", padding=(6, 4), relief="sunken")
        self.status_label.pack(fill=tk.X, pady=(8, 0))

    def add_edit_field(self, parent: ttk.Frame, label_text: str, var: tk.StringVar) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill=tk.X, pady=(0, 8))

        lbl = ttk.Label(frame, text=label_text, style="Panel.TLabel", font=("Segoe UI", 9, "bold"))
        lbl.pack(anchor=tk.W, pady=(0, 2))

        entry = ttk.Entry(frame, textvariable=var, font=("Segoe UI", 10))
        entry.pack(fill=tk.X, ipady=2)

    # ================= SETTINGS & TREEVIEW LOGIC =================

    def refresh_columns_tree(self) -> None:
        # Clear existing
        for item in self.columns_tree.get_children():
            self.columns_tree.delete(item)

        # Insert columns
        for col in self.columns_list:
            field_name = FIELD_OPTIONS.get(col["field"], col["field"])
            required = col.get("required", False)
            required_display = "✅" if required else "❌"
            self.columns_tree.insert(
                "", tk.END, values=(col["header"], field_name, required_display)
            )

    def on_tree_select(self, event: tk.Event) -> None:
        selected = self.columns_tree.selection()
        if not selected:
            return
        item_vals = self.columns_tree.item(selected[0], "values")
        if item_vals and len(item_vals) >= 2:
            self.col_header_var.set(item_vals[0])
            self.col_field_combobox.set(item_vals[1])
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
            self.save_settings(self.sheet_webhook_var.get().strip(), self.columns_list)
            return "break"

    def add_or_update_column(self) -> None:
        header = self.col_header_var.get().strip()
        field_label = self.col_field_combobox.get()
        field_code = REVERSE_FIELD_OPTIONS.get(field_label)

        if not header or not field_code:
            messagebox.showwarning("Validation Error", "Please provide a valid Header Name and select a field.")
            return

        required = self.col_required_var.get()
        column_entry = {"header": header, "field": field_code, "required": required}

        selected = self.columns_tree.selection()
        if selected:
            # Update mode
            index = self.columns_tree.index(selected[0])
            self.columns_list[index] = column_entry
            self.status_var.set(f"Updated column at position {index + 1}.")
        else:
            # Add mode
            # Check if header already exists
            if any(c["header"].lower() == header.lower() for c in self.columns_list):
                messagebox.showwarning("Validation Error", f"A column with header '{header}' already exists.")
                return
            self.columns_list.append(column_entry)
            self.status_var.set(f"Added column '{header}'.")

        self.refresh_columns_tree()
        save_settings(self.sheet_webhook_var.get().strip(), self.columns_list)
        
        # Clear selected state and inputs
        self.columns_tree.selection_remove(self.columns_tree.selection())
        self.col_header_var.set("")

    def remove_column(self) -> None:
        selected = self.columns_tree.selection()
        if not selected:
            messagebox.showwarning("Selection Error", "Please select a column from the list to delete.")
            return

        index = self.columns_tree.index(selected[0])
        removed = self.columns_list.pop(index)
        self.status_var.set(f"Removed column '{removed['header']}'.")

        self.refresh_columns_tree()
        save_settings(self.sheet_webhook_var.get().strip(), self.columns_list)
        self.col_header_var.set("")

    def move_column(self, direction: int) -> None:
        selected = self.columns_tree.selection()
        if not selected:
            return

        index = self.columns_tree.index(selected[0])
        new_index = index + direction

        if 0 <= new_index < len(self.columns_list):
            # Swap
            self.columns_list[index], self.columns_list[new_index] = self.columns_list[new_index], self.columns_list[index]
            self.refresh_columns_tree()
            # Reselect moved item
            item_id = self.columns_tree.get_children()[new_index]
            self.columns_tree.selection_set(item_id)
            save_settings(self.sheet_webhook_var.get().strip(), self.columns_list)

    def on_webhook_change(self, event: tk.Event) -> None:
        save_settings(self.sheet_webhook_var.get().strip(), self.columns_list)

    # ================= SCRAPER & SHEET POSTING =================

    def start_scrape(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("Please enter a website URL.")
            return

        self.scrape_button.configure(state=tk.DISABLED)
        self.status_var.set("Connecting to website and crawling homepage...")
        self.clear_results()

        include_contact = not self.homepage_only_var.get()
        thread = threading.Thread(target=self.scrape_in_background, args=(url, include_contact), daemon=True)
        thread.start()

    def scrape_in_background(self, url: str, include_contact: bool) -> None:
        try:
            result = scrape_company(url, include_contact_pages=include_contact)
        except HTTPError as error:
            result = {"error": f"HTTP {error.code}: {error.reason}"}
        except (OSError, URLError, ValueError) as error:
            result = {"error": str(error)}
        self.result_queue.put(result)

    def poll_results(self) -> None:
        try:
            result = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self.poll_results)
            return

        self.apply_result(result)
        self.scrape_button.configure(state=tk.NORMAL)
        self.after(100, self.poll_results)

    def apply_result(self, result: dict[str, object]) -> None:
        error = result.get("error")
        if error:
            self.status_var.set(f"Error scraping: {error}")
            messagebox.showerror("Scraping Error", f"Could not scrape website details:\n{error}")
            return

        # Store raw result for mapping later
        self.last_scraped_raw = result

        # Apply to UI variables
        self.company_var.set(str(result.get("company_name") or ""))
        self.email_var.set(", ".join(str(e) for e in (result.get("email") or [])))
        self.phone_var.set(", ".join(str(p) for p in (result.get("phone") or [])))
        self.location_var.set(str(result.get("location") or ""))
        self.services_var.set(", ".join(str(s) for s in (result.get("services") or [])))
        
        self.linkedin_var.set(str(result.get("linkedin") or ""))
        self.facebook_var.set(str(result.get("facebook") or ""))
        self.instagram_var.set(str(result.get("instagram") or ""))
        self.twitter_var.set(str(result.get("twitter") or ""))
        self.youtube_var.set(str(result.get("youtube") or ""))
        self.tiktok_var.set(str(result.get("tiktok") or ""))
        
        self.all_socials_var.set(str(result.get("all_socials") or ""))
        self.timestamp_var.set(str(result.get("timestamp") or ""))

        self.status_var.set("Scrape completed successfully. Feel free to edit results before exporting.")

    def clear_results(self) -> None:
        self.company_var.set("")
        self.email_var.set("")
        self.phone_var.set("")
        self.location_var.set("")
        self.services_var.set("")
        self.linkedin_var.set("")
        self.facebook_var.set("")
        self.instagram_var.set("")
        self.twitter_var.set("")
        self.youtube_var.set("")
        self.tiktok_var.set("")
        self.all_socials_var.set("")
        self.timestamp_var.set("")
        self.last_scraped_raw = {}

    def values_from_scraped_result(
        self,
        result: dict[str, object] | None,
        source_url: str,
        forced_location: str = "",
        maps_business_name: str = "",
        maps_url: str = "",
        error: str = "",
    ) -> dict[str, str]:
        result = result or {}
        socials_list = [
            str(result.get("linkedin") or "").strip(),
            str(result.get("facebook") or "").strip(),
            str(result.get("instagram") or "").strip(),
            str(result.get("twitter") or "").strip(),
            str(result.get("youtube") or "").strip(),
            str(result.get("tiktok") or "").strip(),
        ]
        all_socials = ", ".join(value for value in socials_list if value)
        email = ", ".join(str(e) for e in (result.get("email") or [])) or "Contact form only"

        return {
            "maps_business_name": maps_business_name,
            "company_name": str(result.get("company_name") or maps_business_name),
            "source_url": source_url,
            "email": email,
            "phone": ", ".join(str(p) for p in (result.get("phone") or [])),
            "location": forced_location or str(result.get("location") or ""),
            "services": ", ".join(str(s) for s in (result.get("services") or [])),
            "linkedin": str(result.get("linkedin") or ""),
            "facebook": str(result.get("facebook") or ""),
            "instagram": str(result.get("instagram") or ""),
            "twitter": str(result.get("twitter") or ""),
            "youtube": str(result.get("youtube") or ""),
            "tiktok": str(result.get("tiktok") or ""),
            "all_socials": all_socials or str(result.get("all_socials") or ""),
            "timestamp": str(result.get("timestamp") or ""),
            "maps_url": maps_url,
            "error": error,
        }

    def build_sheet_payload(
        self,
        field_values: dict[str, str],
        columns: list[dict[str, str]] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        columns = columns or self.columns_list
        headers = []
        payload_values = {}
        for col in columns:
            header_name = col["header"]
            field_code = col["field"]
            headers.append(header_name)
            payload_values[header_name] = field_values.get(field_code, "")
        return headers, payload_values

    def start_maps_batch(self) -> None:
        if collect_websites_from_google_maps is None:
            messagebox.showerror(
                "Playwright Missing",
                "Playwright is not installed in this environment. Activate your .venv and run: pip install -r src\\requirements-playwright.txt",
            )
            return

        webhook_url = self.sheet_webhook_var.get().strip()
        query = self.maps_query_var.get().strip()
        location = self.maps_location_var.get().strip()

        try:
            max_results = int(self.maps_max_results_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid Range", "Max businesses must be a number.")
            return

        if not webhook_url:
            messagebox.showwarning("Configuration Needed", "Paste your Google Sheet Webhook URL first.")
            return
        if not query or not location:
            messagebox.showwarning("Search Needed", "Enter both a business service and a location.")
            return
        if max_results < 1 or max_results > 100:
            messagebox.showwarning("Invalid Range", "Use a value from 1 to 100.")
            return

        save_settings(webhook_url, self.columns_list)
        self.stop_batch_requested.clear()
        self.maps_batch_button.configure(state=tk.DISABLED)
        self.stop_maps_batch_button.configure(state=tk.NORMAL)
        self.status_var.set("Starting Google Maps batch...")

        thread = threading.Thread(
            target=self.maps_batch_in_background,
            args=(query, location, max_results, webhook_url, self.maps_headless_var.get(), list(self.columns_list)),
            daemon=True,
        )
        thread.start()

    def stop_maps_batch(self) -> None:
        self.stop_batch_requested.set()
        self.status_var.set("Stop requested. The current lead will finish first.")

    def maps_batch_in_background(
        self,
        query: str,
        location: str,
        max_results: int,
        webhook_url: str,
        headless: bool,
        columns: list[dict[str, str]],
    ) -> None:
        try:
            self.batch_queue.put(("status", f"Collecting websites from Google Maps: {query} in {location}"))
            websites = collect_websites_from_google_maps(
                query=query,
                location=location,
                max_results=max_results,
                headed=not headless,
                slow_mo_ms=80,
            )
            self.batch_queue.put(("status", f"Collected {len(websites)} websites. Scraping and exporting..."))

            for index, lead in enumerate(websites, start=1):
                if self.stop_batch_requested.is_set():
                    self.batch_queue.put(("done", f"Stopped after {index - 1} leads."))
                    return

                business_name = str(lead.get("business_name") or "")
                website = str(lead.get("website") or "")
                maps_url = str(lead.get("maps_url") or "")
                self.batch_queue.put(("status", f"[{index}/{len(websites)}] Scraping {business_name or website}"))

                scraped = None
                error = ""
                try:
                    scraped = scrape_company(website)
                except Exception as exc:
                    error = str(exc)

                field_values = self.values_from_scraped_result(
                    scraped,
                    source_url=website,
                    forced_location=location,
                    maps_business_name=business_name,
                    maps_url=maps_url,
                    error=error,
                )

                # -- Check required fields: skip if any required field is missing ---
                missing_required = []
                for col in columns:
                    if col.get("required", False):
                        field_code = str(col.get("field") or "").strip()
                        fv = field_values.get(field_code, "")
                        if not fv.strip() or fv.strip().lower() in ("n/a", "contact form only"):
                            missing_required.append(field_code)

                if missing_required:
                    self.batch_queue.put(
                        (
                            "status",
                            f"SKIPPED ({business_name or website}) — missing required field(s): "
                            f"{', '.join(missing_required)}",
                        )
                    )
                    continue

                headers, payload_values = self.build_sheet_payload(field_values, columns)

                try:
                    send_result_to_sheet(webhook_url, headers, payload_values)
                except Exception as exc:
                    self.batch_queue.put(("status", f"Sheet export failed for {business_name or website}: {exc}"))
                else:
                    self.batch_queue.put(("preview", field_values))
                    self.batch_queue.put(("status", f"Sent {business_name or website} to Google Sheets."))

            self.batch_queue.put(("done", f"Maps batch completed: {len(websites)} leads processed."))
        except Exception as exc:
            self.batch_queue.put(("done", f"Maps batch failed: {exc}"))

    def poll_batch_results(self) -> None:
        try:
            while True:
                event, payload = self.batch_queue.get_nowait()
                if event == "status":
                    self.status_var.set(str(payload))
                elif event == "preview":
                    self.apply_batch_preview(payload)
                elif event == "done":
                    self.status_var.set(str(payload))
                    self.maps_batch_button.configure(state=tk.NORMAL)
                    self.stop_maps_batch_button.configure(state=tk.DISABLED)
        except queue.Empty:
            pass

        self.after(100, self.poll_batch_results)

    def apply_batch_preview(self, values: dict[str, str]) -> None:
        self.company_var.set(values.get("company_name", ""))
        self.url_var.set(values.get("source_url", ""))
        self.email_var.set(values.get("email", ""))
        self.phone_var.set(values.get("phone", ""))
        self.location_var.set(values.get("location", ""))
        self.services_var.set(values.get("services", ""))
        self.linkedin_var.set(values.get("linkedin", ""))
        self.facebook_var.set(values.get("facebook", ""))
        self.instagram_var.set(values.get("instagram", ""))
        self.twitter_var.set(values.get("twitter", ""))
        self.youtube_var.set(values.get("youtube", ""))
        self.tiktok_var.set(values.get("tiktok", ""))
        self.all_socials_var.set(values.get("all_socials", ""))
        self.timestamp_var.set(values.get("timestamp", ""))

    def start_send_to_sheet(self) -> None:
        webhook_url = self.sheet_webhook_var.get().strip()
        if not webhook_url:
            self.status_var.set("Please configure a Google Sheet Webhook URL first.")
            messagebox.showwarning("Configuration Needed", "Please paste your Apps Script Web App Webhook URL in the entry box below.")
            return

        # Prepare values dictionary based on UI state (so manual modifications are taken!)
        # Re-compile socials list based on UI entries
        socials_list = []
        for v in [self.linkedin_var.get(), self.facebook_var.get(), self.instagram_var.get(), self.twitter_var.get(), self.youtube_var.get(), self.tiktok_var.get()]:
            if v.strip():
                socials_list.append(v.strip())
        all_socials_str = ", ".join(socials_list)

        ui_values = {
            "company_name": self.company_var.get().strip(),
            "source_url": self.url_var.get().strip(),
            "email": self.email_var.get().strip(),
            "phone": self.phone_var.get().strip(),
            "location": self.location_var.get().strip(),
            "services": self.services_var.get().strip(),
            "linkedin": self.linkedin_var.get().strip(),
            "facebook": self.facebook_var.get().strip(),
            "instagram": self.instagram_var.get().strip(),
            "twitter": self.twitter_var.get().strip(),
            "youtube": self.youtube_var.get().strip(),
            "tiktok": self.tiktok_var.get().strip(),
            "all_socials": all_socials_str,
            "timestamp": self.timestamp_var.get().strip() or self.last_scraped_raw.get("timestamp", ""),
        }

        # Check if we have anything to send
        if not any(ui_values.values()):
            self.status_var.set("No data to send. Please scrape a website or fill in the values first.")
            return

        # Construct headers and values matching user's treeview order
        headers = []
        payload_values = {}
        for col in self.columns_list:
            header_name = col["header"]
            field_code = col["field"]
            headers.append(header_name)
            # Fetch current value from edited UI entries
            payload_values[header_name] = ui_values.get(field_code, "")

        self.send_sheet_btn.configure(state=tk.DISABLED)
        self.status_var.set("Posting data to Google Sheets...")

        thread = threading.Thread(
            target=self.send_to_sheet_in_background,
            args=(webhook_url, headers, payload_values),
            daemon=True,
        )
        thread.start()

    def send_to_sheet_in_background(
        self,
        webhook_url: str,
        headers: list[str],
        values: dict[str, str],
    ) -> None:
        try:
            send_result_to_sheet(
                webhook_url,
                headers,
                values,
            )
        except HTTPError as error:
            self.sheet_queue.put((False, f"HTTP {error.code}: {error.reason}"))
        except (OSError, URLError, ValueError) as error:
            self.sheet_queue.put((False, str(error)))
        else:
            self.sheet_queue.put((True, "Export completed successfully! Check your spreadsheet."))

    def poll_sheet_results(self) -> None:
        try:
            success, message = self.sheet_queue.get_nowait()
        except queue.Empty:
            self.after(100, self.poll_sheet_results)
            return

        self.send_sheet_btn.configure(state=tk.NORMAL)
        self.status_var.set(message)
        if success:
            messagebox.showinfo("Export Successful", message)
        else:
            messagebox.showerror("Export Failed", f"Could not post row to Google Sheet:\n{message}")
            
        self.after(100, self.poll_sheet_results)


if __name__ == "__main__":
    CompanyScraperApp().mainloop()
