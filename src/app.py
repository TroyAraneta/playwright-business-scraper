from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from datetime import datetime
from urllib.error import HTTPError, URLError

from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import requests
from bs4 import BeautifulSoup


EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)

CONTACT_WORDS = (
    "contact",
    "get in touch",
    "reach us",
    "support",
    "enquiry",
    "enquiries",
)

SERVICE_WORDS = (
    "service",
    "services",
    "solutions",
    "what we do",
    "expertise",
    "offerings",
    "capabilities",
)

SERVICE_HINT_WORDS = (
    "ads",
    "advertising",
    "analytics",
    "branding",
    "content",
    "creative",
    "design",
    "development",
    "email marketing",
    "growth",
    "marketing",
    "media",
    "paid media",
    "ppc",
    "reporting",
    "seo",
    "social",
    "strategy",
)

SKIP_SERVICE_LABELS = {
    "home",
    "about",
    "about us",
    "contact",
    "contact us",
    "blog",
    "news",
    "careers",
    "privacy policy",
    "terms",
    "login",
    "services",
}

SERVICE_CATEGORY_RULES = (
    ("PPC", ("ppc", "paid ads", "paid media", "advertising", "google ads", "adwords", "remarketing", "retargeting")),
    ("SEO", ("seo", "search engine optimization")),
    ("Content Marketing", ("content marketing", "content", "copywriting", "website copywriting")),
    ("Social Media", ("social", "social media", "facebook", "instagram", "linkedin", "tiktok", "reddit ads")),
    ("Websites", ("web design", "webdesign", "website", "web development", "responsive design", "accessibility services")),
    ("Digital Marketing", ("digital marketing",)),
    ("Hosting", ("hosting",)),
    ("Maintenance", ("maintenance", "site health", "performance optimization", "page speed", "page load")),
    ("Security", ("security",)),
    ("Compliance", ("compliance",)),
    ("Mobile Design", ("mobile design", "mobile app", "app design", "app development")),
    ("Branding", ("branding", "brand identity", "brand awareness", "logo design", "typography", "brand guidelines")),
    ("Graphic Design", ("graphic design", "signage", "packaging")),
    ("Analytics", ("analytics", "google analytics", "reporting", "data")),
    ("Creative", ("creative", "art direction")),
    ("Strategy", ("strategy",)),
    ("Email Marketing", ("email marketing",)),
)

SERVICE_NOISE_PHRASES = (
    "case studies",
    "case study",
    "kind words",
    "from our clients",
    "not your traditional",
    "marketing agency",
    "promo",
    "brand awareness",
    "can be increased",
    "faster on average",
    "bounce rate",
    "page load time",
    "audit",
    "healthcare",
    "dental marketing",
    "enterprise seo",
    "e-commerce seo",
    "local seo",
    "rodeo austin",
)

CTA_KEYWORDS = (
    "ready to",
    "get started",
    "let's",
    "discover",
    "explore",
    "start your",
    "learn more",
    "find out",
)

QUESTION_WORDS = ("what", "how", "why", "where", "when", "who", "which")

PERSONAL_PRONOUNS = ("your ", "our ", "we ", "my ")  # trailing space for word-boundary safety


@dataclass
class Link:
    href: str
    text: str
    in_nav_or_header: bool


@dataclass
class TextElement:
    tag: str
    text: str


@dataclass
class ParsedPage:
    url: str
    text: str
    links: list[Link] = field(default_factory=list)
    title: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    elements: list[TextElement] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)



PHONE_PATTERN = re.compile(
    r"\+?\b(?:\d{1,3}[\s.-]?)?\(?\d{2,5}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,9}\b"
)

SOCIAL_PATTERNS = {
    "linkedin": re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in|school|org)/[a-zA-Z0-9-_\.]+/?", re.I),
    "facebook": re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com/[a-zA-Z0-9\.-]+/?", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9-_\.]+/?", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[a-zA-Z0-9_]+/?", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/(?:c/|channel/|user/|@)?[a-zA-Z0-9-_\.]+/?", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[a-zA-Z0-9-_\.]+/?", re.I),
}

def extract_socials(page: ParsedPage) -> dict[str, str]:
    socials = {}
    for link in page.links:
        href = link.href.strip()
        joined = urljoin(page.url, href)
        for name, pattern in SOCIAL_PATTERNS.items():
            if name not in socials:
                match = pattern.search(joined)
                if match:
                    socials[name] = match.group(0).rstrip("/")
    return socials

def extract_phones(page: ParsedPage) -> list[str]:
    phones = set()
    for link in page.links:
        if link.href.lower().startswith("tel:"):
            from urllib.parse import unquote
            phone = unquote(link.href.removeprefix("tel:")).strip()
            phone = re.sub(r"\s+", " ", phone).strip()
            if phone:
                phones.add(phone)
    matches = PHONE_PATTERN.findall(page.text)
    for match in matches:
        clean = match.strip(" .,:;-()[]")
        digits = re.sub(r"\D", "", clean)
        if 7 <= len(digits) <= 15:
            if not (len(digits) == 8 and digits.startswith("202")) and not digits.startswith("0000"):
                phones.add(clean)
    return sorted(list(phones))

def extract_location(page: ParsedPage) -> str | None:
    for key, val in page.meta.items():
        if any(w in key for w in ("address", "locality", "region", "postal-code", "country")):
            if val.strip():
                return val.strip()
    address_keywords = {"street", "st.", "road", "rd.", "avenue", "ave.", "boulevard", "blvd.", "drive", "dr.", "way", "lane", "ln.", "suite", "ste.", "floor", "fl.", "zip code", "postal code"}
    
    chunks = getattr(page, "text_parts", [])
    if not chunks:
        chunks = re.split(r'[;•|]|\. |\s{3,}', page.text)
        
    for chunk in chunks:
        clean = chunk.strip()
        text_lower = clean.lower()
        if any(word in text_lower for word in address_keywords):
            if re.search(r"\d+", clean):
                if 10 < len(clean) < 150:
                    return clean
    return None



class HeuristicHTMLParser(HTMLParser):
    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.links: list[Link] = []
        self.meta: dict[str, str] = {}
        self.elements: list[TextElement] = []
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self.current_link: dict[str, object] | None = None
        self.current_text_tag: str | None = None
        self.current_text_parts: list[str] = []
        self.skip_depth = 0
        self.in_title = False
        self.nav_or_header_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()

        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.skip_depth += 1
            return

        if tag in {"nav", "header"}:
            self.nav_or_header_depth += 1

        if tag == "title":
            self.in_title = True
            self.current_text_tag = tag
            self.current_text_parts = []

        if tag == "meta":
            key = attr_map.get("property") or attr_map.get("name")
            content = attr_map.get("content")
            if key and content:
                self.meta[key.lower()] = normalize_space(content)

        if tag == "a" and attr_map.get("href"):
            self.current_link = {
                "href": attr_map["href"],
                "parts": [],
                "in_nav_or_header": self.nav_or_header_depth > 0,
            }

        if tag in {"h1", "h2", "h3", "h4", "li"}:
            self.current_text_tag = tag
            self.current_text_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in {"script", "style", "noscript", "template", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return

        if tag == "a" and self.current_link:
            parts = self.current_link.get("parts", [])
            self.links.append(
                Link(
                    href=str(self.current_link["href"]),
                    text=normalize_space(" ".join(str(part) for part in parts)),
                    in_nav_or_header=bool(self.current_link["in_nav_or_header"]),
                )
            )
            self.current_link = None

        if tag == "title":
            self.in_title = False
            title = normalize_space(" ".join(self.title_parts))
            if title:
                self.elements.append(TextElement("title", title))

        if tag == self.current_text_tag:
            text = normalize_space(" ".join(self.current_text_parts))
            if text:
                self.elements.append(TextElement(tag, text))
            self.current_text_tag = None
            self.current_text_parts = []

        if tag in {"nav", "header"} and self.nav_or_header_depth:
            self.nav_or_header_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return

        text = unescape(data).strip()
        if not text:
            return

        self.text_parts.append(text)

        if self.in_title:
            self.title_parts.append(text)

        if self.current_link:
            parts = self.current_link["parts"]
            assert isinstance(parts, list)
            parts.append(text)

        if self.current_text_tag:
            self.current_text_parts.append(text)

    def page(self) -> ParsedPage:
        title = normalize_space(" ".join(self.title_parts)) or None
        return ParsedPage(
            url=self.url,
            text=normalize_space(" ".join(self.text_parts)),
            links=self.links,
            title=title,
            meta=self.meta,
            elements=self.elements,
            text_parts=self.text_parts,
        )



def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if not parsed.scheme:
        return f"https://{raw_url}"
    return raw_url


def candidate_urls(raw_url: str) -> list[str]:
    normalized = normalize_url(raw_url)
    parsed = urlparse(normalized)
    if parsed.scheme != "https":
        return [normalized]
    return [normalized, parsed._replace(scheme="http").geturl()]


def fetch_page(url: str, timeout: int = 15) -> ParsedPage:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")

    return parse_html(html, final_url)


def parse_html(html: str, url: str) -> ParsedPage:
    parser = HeuristicHTMLParser(url)
    parser.feed(html)
    parser.close()
    return parser.page()


def extract_emails(page: ParsedPage) -> list[str]:
    text = page.text
    text = re.sub(r"\s*\[\s*at\s*\]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*\(\s*at\s*\)\s*", "@", text, flags=re.I)
    text = re.sub(r"\s+at\s+", "@", text, flags=re.I)
    text = re.sub(r"\s*\[\s*dot\s*\]\s*", ".", text, flags=re.I)
    text = re.sub(r"\s*\(\s*dot\s*\)\s*", ".", text, flags=re.I)
    text = re.sub(r"\s+dot\s+", ".", text, flags=re.I)

    hrefs = " ".join(link.href for link in page.links)
    emails = set(EMAIL_PATTERN.findall(f"{text} {hrefs}"))
    return sorted(clean_email(email) for email in emails if clean_email(email))



def clean_email(email: str) -> str:
    return email.removeprefix("mailto:").strip(" .,:;").lower()


def extract_company_name(page: ParsedPage) -> str | None:
    if page.meta.get("og:site_name"):
        return clean_company_name(page.meta["og:site_name"])

    if page.meta.get("application-name"):
        return clean_company_name(page.meta["application-name"])

    if page.title:
        return clean_company_name(page.title)

    for element in page.elements:
        if element.tag == "h1":
            return clean_company_name(element.text)

    host = urlparse(page.url).netloc.replace("www.", "")
    return host.split(".")[0].replace("-", " ").title() if host else None


def clean_company_name(name: str) -> str:
    cleaned = normalize_space(name)
    for separator in (" | ", " - ", " :: "):
        if separator in cleaned:
            cleaned = cleaned.split(separator)[0].strip()
    return cleaned


def same_site(base_url: str, target_url: str) -> bool:
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    target_host = urlparse(target_url).netloc.lower().removeprefix("www.")
    return base_host == target_host


def find_contact_links(page: ParsedPage) -> list[str]:
    links: list[str] = []
    for link in page.links:
        joined = urljoin(page.url, link.href.strip())
        haystack = f"{link.text} {link.href}".lower()
        if any(word in haystack for word in CONTACT_WORDS) and same_site(page.url, joined):
            links.append(joined)
    return unique_in_order(links)


def extract_services(page: ParsedPage) -> list[str]:
    candidates: list[str] = []
    candidates.extend(service_like_nav_labels(page))
    candidates.extend(service_section_labels(page))
    candidates.extend(service_like_headings(page))
    raw_services = unique_in_order(
        label
        for label in (normalize_label(candidate) for candidate in candidates)
        if label and is_likely_service(label)
    )
    return categorize_services(raw_services)


def service_like_nav_labels(page: ParsedPage) -> Iterable[str]:
    for link in page.links:
        haystack = f"{link.text} {link.href}".lower()
        if not link.in_nav_or_header:
            continue
        if any(word in haystack for word in SERVICE_WORDS) or is_service_hint(link.text):
            yield link.text


def service_section_labels(page: ParsedPage) -> Iterable[str]:
    in_service_section = False
    for element in page.elements:
        text_lower = element.text.lower()

        if element.tag in {"h1", "h2", "h3", "h4"} and any(
            word in text_lower for word in SERVICE_WORDS
        ):
            in_service_section = True
            continue

        if in_service_section and element.tag in {"h1", "h2"}:
            in_service_section = False

        if in_service_section and element.tag in {"h3", "h4", "li"}:
            yield element.text


def service_like_headings(page: ParsedPage) -> Iterable[str]:
    for element in page.elements:
        if element.tag not in {"h2", "h3", "h4"}:
            continue
        if is_service_hint(element.text) and _looks_like_service_noun(element.text):
            yield element.text


_HINT_PATTERNS = [
    re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
    for word in SERVICE_HINT_WORDS
]


def _looks_like_service_noun(label: str) -> bool:
    """Return True if the heading reads like a service name, not a slogan or CTA."""
    stripped = label.strip()
    if '?' in stripped:
        return False
    if re.match(r"^(Ready|Get started|Let's|Discover|Explore|Start your|Learn more|Find out)", stripped, re.IGNORECASE):
        return False
    return True


def is_service_hint(label: str) -> bool:
    lowered = label.lower()
    if lowered in SKIP_SERVICE_LABELS:
        return False
    return any(pattern.search(label) for pattern in _HINT_PATTERNS)


def normalize_label(label: str) -> str:
    label = re.sub(r"\s+", " ", label).strip(" -:|")
    label = re.sub(r"\b(Learn More|Read More|View More|More)\b", "", label, flags=re.I)
    return re.sub(r"\s+", " ", label).strip(" -:|")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_likely_service(label: str) -> bool:
    lowered = label.lower()
    if lowered in SKIP_SERVICE_LABELS:
        return False
    if any(phrase in lowered for phrase in SERVICE_NOISE_PHRASES):
        return False
    if "copyright" in lowered or "(c)" in lowered or "©" in label:
        return False
    if len(label) < 3 or len(label) > 80:
        return False
    if EMAIL_PATTERN.search(label):
        return False
    return bool(re.search(r"[A-Za-z]{3,}", label))


def categorize_services(services: Iterable[str]) -> list[str]:
    categorized: set[str] = set()
    uncategorized: list[str] = []

    for service in services:
        categories = service_categories(service)
        if categories:
            categorized.update(categories)
        else:
            uncategorized.append(service)

    ordered_categories = [
        category
        for category, _keywords in SERVICE_CATEGORY_RULES
        if category in categorized
    ]
    return unique_in_order([*ordered_categories, *uncategorized])


def service_categories(service: str) -> list[str]:
    lowered = service.lower()
    matches: list[str] = []
    for category, keywords in SERVICE_CATEGORY_RULES:
        if any(keyword in lowered for keyword in keywords):
            matches.append(category)
    return matches


def unique_in_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            results.append(item)
    return results


def fetch_homepage(raw_url: str) -> ParsedPage:
    last_error: Exception | None = None
    for url in candidate_urls(raw_url):
        try:
            return fetch_page(url)
        except (HTTPError, OSError, URLError) as error:
            last_error = error

    if last_error:
        raise last_error
    raise ValueError("No URL was provided.")


def scrape_company(url: str, include_contact_pages: bool = True) -> dict[str, object]:
    home = fetch_homepage(url)
    contact_links = find_contact_links(home)

    emails = extract_emails(home)
    services = extract_services(home)
    phones = extract_phones(home)
    location = extract_location(home)
    socials = extract_socials(home)

    if include_contact_pages:
        for contact_url in contact_links[:3]:
            try:
                contact_page = fetch_page(contact_url)
            except (OSError, URLError):
                continue
            emails.extend(extract_emails(contact_page))
            services.extend(extract_services(contact_page))
            phones.extend(extract_phones(contact_page))
            
            contact_socials = extract_socials(contact_page)
            for k, v in contact_socials.items():
                if k not in socials or not socials[k]:
                    socials[k] = v
            
            if not location:
                location = extract_location(contact_page)

    # -- Multi-source email fallback ----------------------------------------------------
    # If no email was found on the website, try the Facebook page, then LinkedIn.
    if not emails:
        fb_email = extract_email_from_facebook(socials.get("facebook", ""))
        if fb_email:
            emails.append(fb_email)

    if not emails:
        li_email = extract_email_from_linkedin(socials.get("linkedin", ""))
        if li_email:
            emails.append(li_email)

    unique_emails = unique_in_order(emails)
    unique_services = unique_in_order(services)
    unique_phones = unique_in_order(phones)

    social_values = [v for v in socials.values() if v]
    all_socials_str = ", ".join(social_values)

    return {
        "company_name": extract_company_name(home),
        "email": unique_emails,
        "services": unique_services,
        "phone": unique_phones,
        "location": location or "",
        "linkedin": socials.get("linkedin", ""),
        "facebook": socials.get("facebook", ""),
        "instagram": socials.get("instagram", ""),
        "twitter": socials.get("twitter", ""),
        "youtube": socials.get("youtube", ""),
        "tiktok": socials.get("tiktok", ""),
        "all_socials": all_socials_str,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }





# -- New: BS4 + requests email extraction and Google Sheets pipeline ---

EMAIL_BS4_PATTERN = re.compile(
    r"[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+[.][a-zA-Z0-9_]+", re.IGNORECASE
)


def _normalize_obfuscated_emails(text: str) -> str:
    """Replace common email obfuscation patterns with normal @ and . characters."""
    text = re.sub(r"\s*\[\s*at\s*\]\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\s*at\s*\)\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+at\s+", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\[\s*dot\s*\]\s*", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\s*dot\s*\)\s*", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+dot\s+", ".", text, flags=re.IGNORECASE)
    return text


def _first_email_from_html(html: str) -> str | None:
    """Return the first valid email address found in HTML text, or None."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    text = _normalize_obfuscated_emails(text)
    matches = EMAIL_BS4_PATTERN.findall(text)
    for match in matches:
        email = match.lower().strip().rstrip(".,;")
        if email:
            return email
    return None


def _normalize_facebook_url(raw_url: str) -> str | None:
    """Clean a Facebook URL and return its /about variant, or None if invalid."""
    if not raw_url:
        return None
    url = raw_url.strip().rstrip("/")
    # Strip query parameters and fragments
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    # Build the about-page URL
    about = clean.rstrip("/") + "/about"
    return about


def extract_email_from_facebook(facebook_url: str) -> str | None:
    """Try to extract an email address from a Facebook business page About section.

    Fetches ``{facebook_url}/about`` using ``requests`` + BeautifulSoup and
    looks for email addresses with ``EMAIL_BS4_PATTERN``.

    Facebook's public business pages (not profiles) render enough HTML without
    JavaScript for the About tab to contain contact info.  If the page redirects
    to a login wall, returns ``None``.

    Parameters
    ----------
    facebook_url:
        The full Facebook page URL, e.g. ``https://www.facebook.com/businessname``.

    Returns
    -------
    str | None
        The first email address found, or ``None``.
    """
    about_url = _normalize_facebook_url(facebook_url)
    if not about_url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        response = requests.get(about_url, headers=headers, timeout=10, allow_redirects=True)
        response.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    # Facebook redirects unauthenticated visitors to a login page — detect it.
    if "login" in response.url.lower():
        return None

    return _first_email_from_html(response.text)


def extract_email_from_linkedin(linkedin_url: str) -> str | None:
    """Try to extract an email address from a LinkedIn company page.

    Fetches ``{linkedin_url}/about`` using ``requests`` + BeautifulSoup and
    looks for email addresses with ``EMAIL_BS4_PATTERN``.

    Many LinkedIn company pages render contact info in the page meta / JSON-LD
    that is visible without JavaScript.  If the page redirects to a login wall,
    returns ``None``.

    Parameters
    ----------
    linkedin_url:
        The full LinkedIn company URL, e.g. ``https://www.linkedin.com/company/name``.

    Returns
    -------
    str | None
        The first email address found, or ``None``.
    """
    if not linkedin_url:
        return None

    url = linkedin_url.strip().rstrip("/")
    # Try the main page first, then /about
    for candidate in (url, url + "/about"):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            response = requests.get(candidate, headers=headers, timeout=10, allow_redirects=True)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            continue

        # LinkedIn also redirects to a login wall.
        if "login" in response.url.lower() or "authwall" in response.url.lower():
            continue

        email = _first_email_from_html(response.text)
        if email:
            return email

    return None


def extract_emails_with_bs4(url: str, timeout: int = 15) -> list[str]:
    """Visit a website and extract email addresses using BeautifulSoup4 + requests.

    Uses the exact regex: r'[\\w.-]+@[\\w.-]+\\.\\w+'
    Handles sites that block requests, missing emails, and missing websites.

    Returns
    -------
    list[str]
        Sorted list of unique, normalized email addresses.
        Empty list if the site blocks requests, the URL is empty, or no emails
        are found.
    """
    if not url:
        return []

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.RequestException):
        # Site blocks requests or is unreachable
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # Extract emails from mailto: links
    mailto_links = soup.find_all("a", href=re.compile(r"^mailto:", re.IGNORECASE))
    mailto_emails: set[str] = set()
    for link in mailto_links:
        href = link.get("href", "")
        email = href.removeprefix("mailto:").strip().lower()
        if email and "@" in email:
            mailto_emails.add(email)

    # Extract emails from visible text using the specified regex
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s*\[\s*at\s*\]\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\s*at\s*\)\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+at\s+", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\[\s*dot\s*\]\s*", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\s*dot\s*\)\s*", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+dot\s+", ".", text, flags=re.IGNORECASE)

    found_emails = EMAIL_BS4_PATTERN.findall(text)

    # Combine, deduplicate, and normalize
    all_emails = set(found_emails) | mailto_emails
    valid_emails = [
        email.lower().strip().rstrip(".,;")
        for email in all_emails
        if "@" in email
    ]

    return sorted(set(valid_emails))


def process_businesses(
    businesses: list[dict[str, str]],
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str = "Sheet1",
) -> list[dict]:
    """Visit each business website, extract emails, and write results to Google Sheets.

    Handles missing websites, missing emails, and sites that block requests.

    When the website yields no email, falls back to the Facebook About page
    (if ``facebook_url`` is provided in the business dict) and then the
    LinkedIn company page (if ``linkedin_url`` is provided).

    Parameters
    ----------
    businesses:
        List of dicts with keys: business_name, rating, website,
        and optionally facebook_url, linkedin_url.
    credentials_path:
        Path to the service account JSON key file.
    spreadsheet_id:
        The Google Sheets document ID (found in the URL).
    sheet_name:
        Name of the worksheet tab to write to.

    Returns
    -------
    list[dict]
        Enriched results with keys: business_name, rating, website, email,
        email_source.
    """
    from sheets import write_results_to_sheet

    results: list[dict] = []
    for business in businesses:
        website = business.get("website", "").strip()
        email_source = "not found"

        # Handle missing websites
        if not website:
            results.append({
                "business_name": business.get("business_name", ""),
                "rating": business.get("rating", ""),
                "website": website,
                "email": "N/A",
                "email_source": "no website",
            })
            continue

        # Step 1 — try the website itself
        found_emails = extract_emails_with_bs4(website)

        # Step 2 — try the /contact page
        if not found_emails:
            try:
                contact_url = urljoin(website, "/contact")
                found_emails = extract_emails_with_bs4(contact_url)
            except Exception:
                pass

        # Step 3 — try Facebook About page
        if not found_emails:
            fb_url = business.get("facebook_url", "").strip()
            if fb_url:
                fb_email = extract_email_from_facebook(fb_url)
                if fb_email:
                    found_emails = [fb_email]
                    email_source = "facebook"

        # Step 4 — try LinkedIn company page
        if not found_emails:
            li_url = business.get("linkedin_url", "").strip()
            if li_url:
                li_email = extract_email_from_linkedin(li_url)
                if li_email:
                    found_emails = [li_email]
                    email_source = "linkedin"

        if found_emails and email_source == "not found":
            email_source = "website"

        results.append({
            "business_name": business.get("business_name", ""),
            "rating": business.get("rating", ""),
            "website": website,
            "email": found_emails[0] if found_emails else "N/A",
            "email_source": email_source,
        })

    write_results_to_sheet(
        credentials_path=credentials_path,
        spreadsheet_id=spreadsheet_id,
        results=results,
        sheet_name=sheet_name,
    )

    return results

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract company name, visible emails, and likely services from a website."
    )
    parser.add_argument("url", help="Website URL, for example https://example.com")
    parser.add_argument(
        "--homepage-only",
        action="store_true",
        help="Do not follow contact page links.",
    )
    args = parser.parse_args()

    try:
        result = scrape_company(args.url, include_contact_pages=not args.homepage_only)
    except HTTPError as error:
        result = {
            "company_name": None,
            "email": [],
            "services": [],
            "error": f"HTTP {error.code}: {error.reason}",
        }
    except (OSError, URLError, ValueError) as error:
        result = {
            "company_name": None,
            "email": [],
            "services": [],
            "error": str(error),
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
