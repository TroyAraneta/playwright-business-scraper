from __future__ import annotations

import argparse
import csv
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote_plus

# ---- PyInstaller-packaged .exe: point Playwright at system-installed browsers ----
# When packaged as a .exe, Playwright's bundled browser detection looks inside the
# temporary _MEI* folder where the extracted chrome binary does not exist.
# Override the search path to the real system location so the installed Chromium
# (from `playwright install`) is found.
_PLAYWRIGHT_BROWSERS_PATH = Path(
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    or Path.home() / "AppData" / "Local" / "ms-playwright"
)
if _PLAYWRIGHT_BROWSERS_PATH.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PLAYWRIGHT_BROWSERS_PATH))

# ---- Playwright availability check ----
# The _greenlet DLL can be blocked by Windows Application Control (AppLocker),
# which causes an ImportError with "DLL load failed" when importing
# playwright.sync_api.  We detect this case and provide a clear error
# at call time instead of failing at module import.
try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError as _pie:
    _PLAYWRIGHT_AVAILABLE = False
    _PLAYWRIGHT_DLL_BLOCKED = (
        "DLL load failed" in str(_pie)
        or "Application Control" in str(_pie)
    )

    # Stub the exception so module-level references still compile
    class PlaywrightTimeoutError(Exception):  # type: ignore[no-redef]
        """Stub placeholder — Playwright is not available."""

    sync_playwright = None  # type: ignore[assignment]


    def _raise_playwright_error() -> None:
        if _PLAYWRIGHT_DLL_BLOCKED:
            raise RuntimeError(
                "Playwright is unavailable because a required DLL (_greenlet) was "
                "blocked by Windows Application Control (AppLocker / SmartScreen).\n\n"
                "To fix this, try one of the following:\n"
                "  1. Run the application as Administrator.\n"
                "  2. Temporarily disable Application Control / AppLocker.\n"
                "  3. Exempt the packaged .exe in your AppLocker policy.\n"
                "The Google Maps batch feature requires Playwright and will not work "
                "until this is resolved."
            )
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -r src\\requirements-playwright.txt"
        )


from app import scrape_company


def _require_playwright() -> None:
    """Raise a clear error if Playwright could not be loaded."""
    if not _PLAYWRIGHT_AVAILABLE:
        _raise_playwright_error()


def _check_browsers_installed() -> None:
    """Verify Playwright browsers exist on disk; raise with 'playwright install' if not.

    Playwright's Python package can import fine even when the browser binaries
    haven't been downloaded yet.  This check catches that case pre-launch.
    """
    try:
        from playwright._impl._registry import Registry
        reg = Registry()
        # Try to resolve the local browser path without downloading
        for name in ("chromium", "chrome", "msedge"):
            try:
                entry = reg.find_executable(name)
                if entry and entry.executable_path:
                    if Path(entry.executable_path).exists():
                        return  # at least one browser is installed
            except Exception:
                continue
    except ImportError:
        pass  # older Playwright — let it fail naturally at launch

    # Fallback: check the manually-specified PLAYWRIGHT_BROWSERS_PATH directory
    browsers_dir = _PLAYWRIGHT_BROWSERS_PATH
    if browsers_dir.is_dir():
        for child in browsers_dir.iterdir():
            if child.is_dir() and any(
                p.exists()
                for p in child.rglob("chrome.exe") if p.is_file()
            ):
                return  # found at least one browser binary

    raise RuntimeError(
        "Playwright is installed but its browser binaries have not been downloaded.\n\n"
        "Run the following command in your terminal:\n\n"
        "    playwright install\n\n"
        "Or if using a virtual environment:\n\n"
        "    python -m playwright install\n\n"
        "After installation, restart the application."
    )


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def find_website_link(page) -> str:
    """Return the external website URL from the currently open Google Maps result."""
    _require_playwright()
    selectors = [
        'a[data-item-id="authority"]',
        'a[aria-label^="Website"]',
        'a[data-tooltip="Open website"]',
    ]

    for selector in selectors:
        link = page.locator(selector).first
        try:
            if link.count() and link.is_visible(timeout=1500):
                href = link.get_attribute("href")
                if href and href.startswith("http"):
                    return href
        except PlaywrightTimeoutError:
            continue

    return ""


def find_rating_and_reviews(page) -> tuple[str, str]:
    """Return (rating, review_count) from the currently open Google Maps detail page.

    Extracts the star rating (e.g. "4.5") and the number of reviews (e.g. "1283")
    from the place detail panel.  Both fields default to "" when not found.
    """
    rating = ""
    review_count = ""

    # --- Rating ---
    # Google Maps renders the star rating in a div[role="img"] whose aria-label
    # contains the number (e.g. aria-label="4.5 star rating").
    try:
        all_img = page.locator('div[role="img"]')
        for i in range(all_img.count()):
            try:
                aria = all_img.nth(i).get_attribute("aria-label") or ""
            except Exception:
                continue
            m = re.search(r'(\d+(?:\.\d+)?)', aria.replace(",", ""))
            if m:
                val = float(m.group(1))
                if 0 < val <= 5:  # star ratings are always in this range
                    rating = m.group(1)
                    break
    except PlaywrightTimeoutError:
        pass

    # Fallback: some mobile/alternate layouts use a plain span
    if not rating:
        try:
            span_els = page.locator('span[aria-label*="star" i]')
            if span_els.count():
                aria = span_els.first.get_attribute("aria-label") or ""
                m = re.search(r'(\d+(?:\.\d+)?)', aria)
                if m:
                    rating = m.group(1)
        except PlaywrightTimeoutError:
            pass

    # --- Review count ---
    # The review count lives in a <button> or <span> with "review" in its
    # accessible name (e.g. aria-label="1,283 reviews").
    for selector in (
        'button[aria-label*="review" i]',
        'span[aria-label*="review" i]',
    ):
        try:
            el = page.locator(selector).first
            if el.count():
                aria = el.get_attribute("aria-label") or el.inner_text()
                digits = re.sub(r"[^\d]", "", aria)
                if digits:
                    review_count = digits
                    break
        except PlaywrightTimeoutError:
            continue

    return rating, review_count


def collect_result_links(page, max_results: int, stop_event: threading.Event | None = None) -> list[dict[str, str]]:
    """Collect visible Google Maps place result links with business names after scrolling the result feed.

    Returns
    -------
    list[dict[str, str]]
        Each entry has ``url`` (place page URL) and ``name`` (business name from card, may be empty).
    """
    _require_playwright()
    feed = page.locator('[role="feed"]').first
    for _ in range(min(8, max(2, max_results // 5 + 2))):
        if stop_event and stop_event.is_set():
            break
        try:
            feed.hover(timeout=2000)
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(900)
        except PlaywrightTimeoutError:
            break

        cards = page.evaluate("""(maxR) => {
            const anchors = document.querySelectorAll('a[href*="/maps/place/"]');
            const seen = new Set();
            const results = [];
            for (const a of anchors) {
                if (seen.has(a.href)) continue;
                seen.add(a.href);
                const text = (a.innerText || '').trim();
                const lines = text.split('\\n').filter(l => l.trim());
                results.push({ url: a.href, name: (lines[0] || '').trim() });
            }
            return results.slice(0, maxR);
        }""", max_results)
        if len(cards) >= max_results:
            return cards[:max_results]

    return page.evaluate("""(maxR) => {
        const anchors = document.querySelectorAll('a[href*="/maps/place/"]');
        const seen = new Set();
        const results = [];
        for (const a of anchors) {
            if (seen.has(a.href)) continue;
            seen.add(a.href);
            const text = (a.innerText || '').trim();
            const lines = text.split('\\n').filter(l => l.trim());
            results.push({ url: a.href, name: (lines[0] || '').trim() });
        }
        return results.slice(0, maxR);
    }""", max_results)


def collect_websites_from_google_maps(
    query: str,
    location: str,
    max_results: int,
    headed: bool,
    slow_mo_ms: int,
    stop_event: threading.Event | None = None,
    processed_names: set[str] | None = None,
    processed_websites: set[str] | None = None,
) -> list[dict[str, str]]:
    """Collect business website URLs from a Google Maps search using Playwright.

    Parameters
    ----------
    processed_names:
        Optional set of normalized business names to skip (already Accepted or Rejected).
    processed_websites:
        Optional set of normalized website domains to skip.
    """
    _require_playwright()
    _check_browsers_installed()
    processed_names = processed_names or set()
    processed_websites = processed_websites or set()
    search = quote_plus(f"{query} {location}".strip())
    maps_url = f"https://www.google.com/maps/search/{search}"

    results: list[dict[str, str]] = []
    skipped_from_cache = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, slow_mo=slow_mo_ms)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.goto(maps_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        if "sorry" in page.url.lower() or "captcha" in page.content().lower():
            browser.close()
            raise RuntimeError("Google showed a verification page. Stop and try again manually later.")

        try:
            place_cards = collect_result_links(page, max_results, stop_event=stop_event)

            for card_idx, card in enumerate(place_cards, start=1):
                if stop_event and stop_event.is_set():
                    break

                place_url = card["url"]
                card_name = card["name"].strip().lower()

                # Early cache check: skip if business name from result card is known
                if card_name and processed_names and card_name in processed_names:
                    skipped_from_cache += 1
                    print(f"[{card_idx}] Skipped (cached name): {card['name']}")
                    continue

                page.goto(place_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(600)

                name = ""
                try:
                    name = clean_text(page.locator("h1").first.inner_text(timeout=3000))
                except PlaywrightTimeoutError:
                    pass

                # Second cache check with reliable h1 name
                norm_name = name.strip().lower()
                if norm_name and processed_names and norm_name in processed_names:
                    skipped_from_cache += 1
                    print(f"[{card_idx}] Skipped (h1 name in cache): {name}")
                    continue

                rating, review_count = find_rating_and_reviews(page)
                website = find_website_link(page)

                # Third cache check with website domain
                norm_website = ""
                if website:
                    norm_website = website.strip().lower()
                    if norm_website.startswith(("http://", "https://")):
                        norm_website = norm_website.split("://", 1)[1]
                    if norm_website.startswith("www."):
                        norm_website = norm_website[4:]
                    norm_website = norm_website.split("?")[0].split("#")[0].rstrip("/")
                    if norm_website and processed_websites and norm_website in processed_websites:
                        skipped_from_cache += 1
                        print(f"[{card_idx}] Skipped (cached website): {name} -> {website}")
                        continue

                extra = ""
                if rating:
                    extra += f" ★{rating}"
                if review_count:
                    extra += f" ({review_count} reviews)"

                if website:
                    results.append(
                        {
                            "business_name": name,
                            "maps_url": place_url,
                            "website": website,
                            "rating": rating,
                            "review_count": review_count,
                            "search_location": location,
                        }
                    )
                    print(
                        f"[{card_idx}] Found website:{extra} {name or '(unknown)'} -> {website}"
                    )
                else:
                    print(f"[{card_idx}] No website button found:{extra} {name or place_url}")

                # Minimal polite delay; sleep was a major sequential bottleneck.
                time.sleep(0.2)

        finally:
            browser.close()

    if skipped_from_cache:
        print(f"[Cache] Skipped {skipped_from_cache} already-processed businesses.")

    return results


def _scrape_single_website(row: dict[str, str]) -> dict[str, str]:
    """Scrape a single website and return an enriched row.

    Designed for use inside ThreadPoolExecutor.
    """
    website = row["website"]
    try:
        result = scrape_company(website)
    except Exception as error:
        return {
            **row,
            "company_name": "",
            "email": "",
            "phone": "",
            "location": "",
            "services": "",
            "instagram": "",
            "facebook": "",
            "linkedin": "",
            "twitter": "",
            "youtube": "",
            "tiktok": "",
            "error": str(error),
        }

    return {
        **row,
        "company_name": str(result.get("company_name") or ""),
        "email": ", ".join(result.get("email") or []),
        "phone": ", ".join(result.get("phone") or []),
        "location": str(result.get("location") or ""),
        "services": ", ".join(result.get("services") or []),
        "instagram": str(result.get("instagram") or ""),
        "facebook": str(result.get("facebook") or ""),
        "linkedin": str(result.get("linkedin") or ""),
        "twitter": str(result.get("twitter") or ""),
        "youtube": str(result.get("youtube") or ""),
        "tiktok": str(result.get("tiktok") or ""),
        "error": "",
    }


def scrape_websites(
    websites: list[dict[str, str]],
    max_workers: int = 5,
    stop_event: threading.Event | None = None,
) -> list[dict[str, str]]:
    """Scrape multiple websites in parallel using a thread pool.

    Each website is scraped concurrently because the work is I/O-bound
    (HTTP requests).  The ThreadPoolExecutor maps an equal amount of
    worker threads to the number of websites, bounded by max_workers.

    When *stop_event* is provided and becomes set, pending (not-yet-started)
    website scrapes are cancelled and partial results are returned immediately.
    """
    if not websites:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_scrape_single_website, w) for w in websites]
        results: list[dict[str, str]] = []
        for f in futures:
            if stop_event and stop_event.is_set():
                # Cancel any not-yet-running futures
                for remaining in futures:
                    remaining.cancel()
                break
            try:
                results.append(f.result())
            except Exception:
                # Should not happen — _scrape_single_website catches all errors
                # and returns an error row — but handle defensively.
                pass
        return results


def save_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect website URLs from Google Maps, then optionally run them through the existing website scraper."
    )
    parser.add_argument("query", help='Business type, for example "marketing agency"')
    parser.add_argument("location", help='Location, for example "Austin Texas"')
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--output", default="maps_leads.csv")
    parser.add_argument("--skip-scrape", action="store_true", help="Only collect website URLs.")
    parser.add_argument("--headless", action="store_true", help="Run browser in the background.")
    parser.add_argument("--slow-mo", type=int, default=50, help="Delay browser actions in milliseconds.")
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Maximum concurrent scraper threads (default: 10).",
    )
    args = parser.parse_args()

    try:
        websites = collect_websites_from_google_maps(
            query=args.query,
            location=args.location,
            max_results=args.max_results,
            headed=not args.headless,
            slow_mo_ms=args.slow_mo,
        )
    except RuntimeError as error:
        print(f"Error: {error}")
        return 1

    rows = websites if args.skip_scrape else scrape_websites(websites, max_workers=args.workers)
    save_csv(Path(args.output), rows)
    print(f"\nSaved {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
