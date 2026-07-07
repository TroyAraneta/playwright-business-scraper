# Lead Generation Toolkit

A Python application for extracting business contact data from company websites and collecting leads from Google Maps. Uses heuristic-based HTML parsing — no AI APIs required.

The toolkit has two complementary workflows:

1. **Single-URL Scraper** — Enter any business website and extract company name, email, phone, location, services, and social media profiles.
2. **Google Maps Batch Collector** — Search Google Maps for businesses by type and location, scrape each website found, and export qualified leads to Google Sheets.

Both workflows include a graphical interface, editable results, configurable column mapping, and Google Sheets integration.

---

## Features

- **Website scraping** — Extracts company name, email(s), phone number(s), address/location, services offered, and social media links (LinkedIn, Facebook, Instagram, Twitter/X, YouTube, TikTok) from a company website.
- **Contact page discovery** — Automatically finds and follows contact/enquiry/support pages for richer data.
- **Multi-source email fallback** — If no email is found on the website, attempts extraction from the Facebook About page and LinkedIn company page.
- **Service categorization** — Recognizes over 15 service categories (SEO, PPC, Social Media, Branding, etc.) using heuristic pattern matching.
- **Google Maps lead collection** — Searches Google Maps using Playwright headless browser, collects business listings with ratings and review counts, and scrapes each website found.
- **Multi-location queue** — Run the same search across multiple cities in a single batch.
- **Duplicate prevention** — Deduplication across locations within a run and across runs via a processed-leads cache read from Google Sheets.
- **Required field validation** — Skip leads that are missing required data (e.g., no email found) and optionally log them to a Rejected sheet.
- **Google Sheets export** — Two integration methods: a lightweight Apps Script webhook (no authentication setup) and a gspread service account path for advanced features (cache loading, rejected tracking, summary).
- **Configurable column mapping** — Map any scraped field to any column header before exporting.
- **Editable results** — Modify scraped data in the GUI before sending to Sheets.
- **Graphical and command-line interfaces** — Use the Tkinter GUI or run directly from a terminal.
- **Performance options** — Configurable concurrency (website scraping via thread pool) and headless browser mode.
- **Test suite** — Unit tests for extracting company name, emails, services, social links, phone numbers, and locations.

---

## Architecture

```
src/
├── app.py                     # Core scraping engine
├── gui.py                     # Single-URL scraper GUI
├── maps_website_collector.py  # Google Maps browser automation + batch scraping
├── maps_batch_gui.py          # Maps batch collector GUI
├── sheets.py                  # Google Sheets integration (gspread + webhook)
├── google_apps_script.gs      # Apps Script web app for sheet export
├── tests.py                   # Unit tests
├── benchmark_scrape.py        # Parallel scraping benchmark
├── measure_scrape.py          # Single-threaded scrape timing
├── requirements.txt           # Core dependencies
├── requirements-playwright.txt # Playwright dependencies
├── run.bat                    # CLI launcher
├── run_gui.bat                # Single-URL GUI launcher
├── run_maps_batch_gui.bat     # Maps batch GUI launcher
└── maps_batch_gui.spec        # PyInstaller spec for .exe build
```

### Core scraping engine (`app.py`)

The scraper uses a custom `HTMLParser` subclass (`HeuristicHTMLParser`) that:

- Strips script, style, noscript, and template elements
- Tracks navigation/header context to weight link importance
- Collects meta tags, headings, link text, and raw text parts
- Extracts email addresses from visible text and `mailto:` links, including obfuscated formats (`[at]`, `(dot)`, ` at `)
- Identifies social media profile URLs via pattern matching for six platforms
- Extracts phone numbers from `tel:` links and text patterns
- Detects addresses through common street/keyword heuristics
- Categorizes services using keyword-to-category rules (SEO, PPC, Branding, etc.)

**Scraping flow:**

1. Fetch homepage (tries HTTPS first, falls back to HTTP)
2. Extract company name from meta tags → title → h1 → domain name
3. Extract emails, phone numbers, location, services, and social links
4. Find same-site contact links and repeat extraction on up to 3 contact pages
5. If no email found, attempt Facebook About page, then LinkedIn company page
6. Return all results in a structured dictionary

### Google Maps collector (`maps_website_collector.py`)

Uses Playwright (Chromium) to:

1. Navigate to `google.com/maps/search/{query}+{location}`
2. Scroll the results feed to load enough business listings
3. Visit each business detail page and extract name, rating, review count, and website URL
4. Run each collected website through the scraping engine
5. Support parallel website scraping via `ThreadPoolExecutor` (configurable worker count)
6. Save results to CSV or pass to the GUI for sheet export

---

## Dependencies

| Package | Required For |
|---|---|
| `beautifulsoup4` | Email extraction from Facebook/Linkedin fallback |
| `requests` | HTTP requests for Facebook/LinkedIn fallback |
| `gspread` | Google Sheets read/write (cache, rejected, summary) |
| `google-auth` | Service account authentication |
| `playwright` | Google Maps browser automation |

### Required Python version

Python 3.10 or newer.

---

## Installation

### 1. Clone the repository

```powershell
git clone <repo-url>
cd Lead Generation Scraper
```

### 2. Set up a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. Install dependencies

```powershell
pip install -r src\requirements.txt
```

If you plan to use the Google Maps batch collector:

```powershell
pip install -r src\requirements-playwright.txt
python -m playwright install
```

---

## Usage

### GUI — Single-URL Scraper

```powershell
.\src\run_gui.bat
```

Or directly:

```powershell
python src\gui.py
```

Paste a company website URL, click **Scrape Website**, and the app populates editable fields for company name, email, phone, location, services, and social links. You can edit any field before exporting.

The **Google Sheets Column Configuration** panel lets you define which columns to export and how they map to scraped fields. Add, remove, and reorder columns, then click **Send to Sheet**.

### GUI — Google Maps Batch Collector

```powershell
.\src\run_maps_batch_gui.bat
```

Or directly:

```powershell
python src\maps_batch_gui.py
```

1. Enter a business service (e.g., "marketing agency") and configure max results.
2. Add one or more locations to the queue (e.g., "Austin, Texas", "Dallas, Texas").
3. Configure column mappings and set required fields.
4. Click **Start Batch**.
5. The app searches Google Maps, collects business listings, scrapes each website, and sends qualified leads to Google Sheets.
6. Leads missing required fields are automatically logged to a **Rejected** sheet (if service account credentials are configured).

### CLI — Single URL

```powershell
python src\app.py https://example.com
```

Use `--homepage-only` to skip following contact page links:

```powershell
python src\app.py https://example.com --homepage-only
```

Output is JSON:

```json
{
  "company_name": "Example Corp",
  "email": ["hello@example.com", "sales@example.com"],
  "services": ["SEO", "Websites", "Branding"],
  "phone": ["+1 (555) 123-4567"],
  "location": "123 Main Street, Suite 400, Austin, TX 78701",
  "linkedin": "https://www.linkedin.com/company/example",
  "facebook": "https://facebook.com/example",
  "instagram": "https://instagram.com/example",
  "twitter": "",
  "youtube": "",
  "tiktok": "",
  "all_socials": "https://www.linkedin.com/company/example, https://facebook.com/example",
  "timestamp": "2026-07-07 14:30:00"
}
```

### CLI — Google Maps Batch

```powershell
python src\maps_website_collector.py "marketing agency" "Austin Texas" --max-results 30
```

Add `--skip-scrape` to collect website URLs only (no per-website scraping):

```powershell
python src\maps_website_collector.py "plumber" "Chicago" --max-results 20 --skip-scrape
```

Add `--headless` to run the browser in the background:

```powershell
python src\maps_website_collector.py "dentist" "Miami" --max-results 50 --headless
```

Output is saved to a CSV file (default: `maps_leads.csv`). Use `--workers` to control concurrency (default: 10).

### Running tests

```powershell
python src\tests.py
```

---

## Google Sheets Integration

The application supports two Google Sheets integration methods:

### Method 1: Apps Script Webhook (simpler)

Use this for basic export — sending a single row per scrape to a sheet.

1. Open your Google Sheet.
2. Go to **Extensions → Apps Script**.
3. Paste the contents of `src/google_apps_script.gs`.
4. Click **Deploy → New deployment**.
5. Choose type **Web app**.
6. Set **Execute as** to `Me`.
7. Set **Who has access** to `Anyone`.
8. Deploy, authorize, and copy the **Web App URL**.
9. Paste that URL into the **Google Sheet Webhook URL** field in the app.

The Apps Script creates an **Accepted** sheet (or uses the active sheet) and appends rows with whatever column headers you have configured. It automatically expands columns when new headers are sent.

### Method 2: Service Account (advanced)

Use this for duplicate prevention, rejected-lead tracking, and summary reports.

1. Create a Google Cloud service account and enable the Google Sheets API.
2. Download the JSON key file.
3. Share your target spreadsheet with the service account email (as an editor).
4. In the Maps Batch GUI, set the **Service Account JSON** path and **Spreadsheet ID** fields.

When configured, the app:

- Loads already-processed leads from the **Accepted** and **Rejected** sheets into a cache at startup.
- Skips businesses already present in either sheet during collection.
- Appends leads that fail required-field validation to a **Rejected** sheet with the reason.
- Optionally writes a **Summary** tab with aggregate statistics (companies found, qualified leads, emails found, duplicates removed, elapsed time).

---

## Configuration

### Settings persistence

Settings are saved automatically to JSON files in the `logs/` directory:

| File | Purpose |
|---|---|
| `logs/scraper_settings.json` | Webhook URL and column mapping for the single-URL GUI |
| `logs/maps_batch_settings.json` | Full batch configuration (query, location queue, max results, webhook, credentials, columns) |

### Column mapping

Both GUIs include a column configuration panel where you can:

- Add columns with custom headers (e.g., "Company Email" → `email`)
- Map each column to any scraped field (20 available fields)
- Mark columns as **Required** — leads missing required fields are skipped or rejected
- Reorder columns to match your spreadsheet layout
- Remove or update existing columns

### Available scraped fields

| Field Code | Description |
|---|---|
| `maps_business_name` | Google Maps business listing name |
| `company_name` | Company name extracted from website |
| `source_url` | The website URL |
| `email` | Company email(s) |
| `phone` | Phone number(s) |
| `location` | Address / location |
| `services` | Services offered (categorized) |
| `rating` | Google Maps star rating |
| `review_count` | Google Maps review count |
| `linkedin` | LinkedIn profile URL |
| `facebook` | Facebook page URL |
| `instagram` | Instagram profile URL |
| `twitter` | Twitter/X profile URL |
| `youtube` | YouTube channel URL |
| `tiktok` | TikTok profile URL |
| `all_socials` | All social URLs combined |
| `maps_url` | Google Maps place URL |
| `timestamp` | When the scrape was performed |
| `error` | Error message (if scraping failed) |

---

## Packaging (PyInstaller)

The Maps Batch GUI can be packaged as a standalone Windows executable:

```powershell
pip install pyinstaller
pyinstaller src\maps_batch_gui.spec
```

The spec file is preconfigured with hidden imports for BeautifulSoup, requests, gspread, and Google Auth libraries. The output executable is placed in `src/dist/maps_batch_gui.exe`.

When running as a packaged .exe, Playwright's browser detection may need the `PLAYWRIGHT_BROWSERS_PATH` environment variable pointing to where `playwright install` downloaded Chromium (default: `%USERPROFILE%\AppData\Local\ms-playwright`).

---

## Project Status

The application is functional and in active use. Key design decisions:

- **No AI dependency** — all extraction uses regex, HTML structure analysis, and keyword matching.
- **Heuristic-based service categorization** — services are identified by keywords in navigation labels, section headings, and content, then mapped to standard categories.
- **Privacy-conscious** — the Google Maps collector uses a real browser with polite delays and rate limiting. No API keys are required for Maps data.
- **Graceful degradation** — missing contact pages, blocked requests, and login walls are handled without crashing. Missing fields return empty strings rather than raising errors.

<!--

## Portfolio Notes

### Features that appear incomplete

- The "Keep Screen Awake" and "Include Summary" checkboxes in `maps_batch_gui.py` are functional but lack UI polish (no tooltips, no disabled states when credentials are missing).
- The benchmark (`benchmark_scrape.py`) and timing (`measure_scrape.py`) scripts reference hardcoded CSV file paths from `logs/` — they are developer tools, not polished features, and would benefit from command-line arguments.
- The `maps_batch_gui.py` has TEMP_PAYLOAD and TEMP_WEBHOOK debug logging statements still present (lines 986-1009). These should be removed or gated behind a verbose flag.
- No progress indicator exists for the single-URL GUI's scraping operation beyond a status text label.
- There is no bulk import feature (e.g., upload a CSV of URLs to scrape).
- The Facebook/LinkedIn email fallback is a best-effort feature and may fail on login-walled pages — this is documented in the code but worth noting.

### Missing documentation

- No CONTRIBUTING.md or code of conduct.
- No LICENSE file is present in the repository.
- No CI/CD configuration (GitHub Actions, etc.).
- No `setup.py` or `pyproject.toml` for pip-installable packaging.
- Environment variable configuration is not documented (e.g., `PLAYWRIGHT_BROWSERS_PATH` is mentioned only in source code comments).

### Opportunities to improve the repository for job applications

1. **Add a LICENSE file** (MIT is standard for portfolio projects).
2. **Add a `pyproject.toml`** so the package can be installed with `pip install -e .`.
3. **Remove TEMP_PAYLOAD and TEMP_WEBHOOK debug log lines** from `maps_batch_gui.py` — these look like unfinished instrumentation.
4. **Add a `.gitignore`** for `logs/` (settings files contain credentials/API keys), `__pycache__/`, `.venv/`, and build artifacts.
5. **Replace hardcoded paths** in benchmark scripts with command-line arguments.
6. **Consider adding type hints** throughout — only `app.py` and `maps_batch_gui.py` have full type annotations.
7. **Refactor the two GUIs** — there is significant duplicated code between `gui.py` and `maps_batch_gui.py` (column mapping UI, settings persistence, sheet export logic) that could share a base class or utility module.
8. **Add a `--version` flag** and version constant.
9. **Add a demo GIF or screenshot** to the README — visual proof of the GUIs in action is compelling for employers.
10. **Remove the TASK.md file** if it contains internal notes not relevant to readers.

-->
