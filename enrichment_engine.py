"""V227: Production-grade contractor enrichment engine.

Waterfall strategy (free sources only, no paid APIs):
  1. DuckDuckGo HTML search — extract phone from snippet + candidate website
  2. Candidate website scrape — visit /contact, /about, / for phone+email
  3. Cache every attempt so we don't re-query the same name within 30 days

Libraries:
  - httpx: HTTP/2 client with connection pooling, replaces `requests`
  - selectolax: C-backed HTML parser, ~30x faster than BeautifulSoup
  - tenacity: retry with exponential backoff for transient network errors
  - pybreaker: circuit breakers so a rate-limited source stops blocking
    the whole batch

Compatible with the existing FreeEnrichmentEngine interface so callers
(daemon enrich_batch, /api/admin/enrich) keep working unchanged — this
module is the backing implementation, but `web_enrichment.FreeEnrichmentEngine`
continues to expose the same `enrich_one(name, city, state)` surface.

Note: `duckduckgo-search` library is intentionally NOT used (causes Render
build failures — same reason it was removed in V160). We POST to
html.duckduckgo.com directly, matching the V210 pattern.
"""
from __future__ import annotations

import hashlib
import html as _html
import logging
import re
import time
import urllib.parse
from datetime import datetime, timedelta

import httpx
import pybreaker
from selectolax.parser import HTMLParser
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit breakers — one per external source. Shared across the process so a
# DDG rate-limit trips the breaker for every subsequent call, not just the
# one currently running.
# ---------------------------------------------------------------------------
ddg_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=300,  # 5-minute cooldown between half-open probes
    name="duckduckgo",
)
website_scrape_breaker = pybreaker.CircuitBreaker(
    fail_max=10,
    reset_timeout=600,
    name="website_scrape",
)

# V227 T4: per-city circuit breakers for the collection pipeline.
# Collection callers (violation_collector, daemon force-collect) can wrap
# their per-city API call via `call_with_city_breaker(city_slug, fn)` so a
# single city's upstream failure (timeout, 5xx, bad JSON) stops hammering
# that source for an hour instead of racking up failures every 3-hour cycle.
_city_breakers: dict[str, pybreaker.CircuitBreaker] = {}

def get_city_breaker(city_slug: str) -> pybreaker.CircuitBreaker:
    """Lazy-create a breaker per city_slug. 3 consecutive failures trips it
    for 1 hour. Breakers are process-local (reset on deploy)."""
    breaker = _city_breakers.get(city_slug)
    if breaker is None:
        breaker = pybreaker.CircuitBreaker(
            fail_max=3,
            reset_timeout=3600,  # 1 hour
            name=f"collect_{city_slug}",
        )
        _city_breakers[city_slug] = breaker
    return breaker

def call_with_city_breaker(city_slug: str, fn, *args, **kwargs):
    """Invoke `fn(*args, **kwargs)` under the city's breaker.
    Returns (result, circuit_state). On CircuitBreakerError, result is None
    and circuit_state is 'open'. All other exceptions propagate — the
    caller's existing error handling stays in charge of the retry story."""
    breaker = get_city_breaker(city_slug)
    try:
        out = breaker.call(fn, *args, **kwargs)
        return out, "closed"
    except pybreaker.CircuitBreakerError:
        return None, "open"

# ---------------------------------------------------------------------------
# Patterns + denylists
# ---------------------------------------------------------------------------
PHONE_PATTERN = re.compile(
    r"(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})"
)
EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)
# Phone numbers that are always junk (toll-free, directory assistance, etc)
JUNK_PHONE_PREFIXES = {"800", "888", "877", "866", "855", "844", "833", "822", "411", "911", "555"}
JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "googleapis.com", "schema.org", "w3.org",
    "facebook.com", "twitter.com", "instagram.com", "google.com",
    "apple.com", "microsoft.com", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "duckduckgo.com",
}
# Domains to skip when picking a candidate contractor website
DIRECTORY_DOMAINS = {
    "yelp.com", "yellowpages.com", "bbb.org", "facebook.com",
    "linkedin.com", "twitter.com", "instagram.com", "angi.com",
    "homeadvisor.com", "thumbtack.com", "google.com", "mapquest.com",
    "indeed.com", "glassdoor.com", "mapquest.com", "tripadvisor.com",
    "zillow.com", "yellowbook.com", "superpages.com", "manta.com",
}

DDG_URL = "https://html.duckduckgo.com/html/"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    n = name.lower().strip()
    for suffix in (" llc", " inc", " corp", " ltd", " co", " company", " & sons", " and sons"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return re.sub(r"[^a-z0-9\s]", "", n).strip()


def _cache_key(name: str, city: str, state: str) -> str:
    key = f"{_normalize_name(name)}|{(city or '').lower().strip()}|{(state or '').lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def _format_phone(groups) -> str | None:
    a, b, c = groups
    if a in JUNK_PHONE_PREFIXES:
        return None
    if a.startswith("0") or a.startswith("1"):
        return None
    return f"({a}) {b}-{c}"


def _first_phone(text: str) -> str | None:
    for m in PHONE_PATTERN.finditer(text or ""):
        p = _format_phone(m.groups())
        if p:
            return p
    return None


def _first_email(text: str) -> str | None:
    for m in EMAIL_PATTERN.finditer(text or ""):
        email = m.group(0)
        domain = email.split("@", 1)[-1].lower()
        if domain in JUNK_EMAIL_DOMAINS:
            continue
        return email
    return None


class EnrichmentResult:
    """Structured result from a single enrichment attempt."""

    __slots__ = ("phone", "website", "email", "source", "attempted_sources", "errors")

    def __init__(self):
        self.phone: str | None = None
        self.website: str | None = None
        self.email: str | None = None
        self.source: str | None = None
        self.attempted_sources: list[str] = []
        self.errors: list[str] = []

    @property
    def success(self) -> bool:
        return bool(self.phone or self.website or self.email)

    def to_dict(self) -> dict:
        return {
            "phone": self.phone,
            "website": self.website,
            "email": self.email,
            "source": self.source,
            "attempted_sources": list(self.attempted_sources),
            "errors": list(self.errors),
            "success": self.success,
        }


class ProductionEnrichmentEngine:
    """Multi-source enrichment with circuit breakers, retries, and caching.

    Drop-in alternative to FreeEnrichmentEngine in web_enrichment.py.
    Use `enrich_one(name, city, state) -> (phone, website)` for
    backwards compatibility with the existing daemon/admin callers, or
    `enrich_profile(name, city, state) -> EnrichmentResult` for the
    richer structured return.
    """

    def __init__(self, db_connection_func=None):
        """
        Args:
            db_connection_func: optional callable returning a sqlite3 conn.
              When provided, results are cached for 30 days so repeat
              lookups of the same (name, city, state) skip the network.
        """
        self.get_db = db_connection_func
        self.client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
            http2=False,  # httpx's http2 needs `h2` extra; skip for build simplicity
        )
        if self.get_db is not None:
            try:
                self._ensure_cache_tables()
            except Exception as e:
                logger.warning("V227: could not initialise enrichment_cache tables: %s", e)

    # ---- cache schema + helpers --------------------------------------------

    def _ensure_cache_tables(self) -> None:
        db = self.get_db()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS enrichment_cache (
                cache_key TEXT PRIMARY KEY,
                name TEXT,
                city TEXT,
                state TEXT,
                phone TEXT,
                website TEXT,
                email TEXT,
                source TEXT,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success INTEGER DEFAULT 0
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS enrichment_failures (
                cache_key TEXT PRIMARY KEY,
                name TEXT,
                city TEXT,
                state TEXT,
                attempt_count INTEGER DEFAULT 0,
                last_attempted TIMESTAMP,
                errors TEXT,
                next_retry_after TIMESTAMP
            )
            """
        )
        db.commit()

    def _is_cached(self, cache_key: str) -> bool:
        if self.get_db is None:
            return False
        cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            row = self.get_db().execute(
                "SELECT 1 FROM enrichment_cache WHERE cache_key = ? AND attempted_at > ?",
                (cache_key, cutoff),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _is_exhausted(self, cache_key: str) -> bool:
        if self.get_db is None:
            return False
        try:
            row = self.get_db().execute(
                "SELECT attempt_count, next_retry_after FROM enrichment_failures WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                return False
            attempts, retry_after = (row[0], row[1]) if not hasattr(row, "keys") else (row["attempt_count"], row["next_retry_after"])
            if attempts and attempts >= 4 and retry_after:
                return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") < retry_after
        except Exception:
            pass
        return False

    def _save_result(self, cache_key: str, name: str, city: str, state: str, result: EnrichmentResult) -> None:
        if self.get_db is None:
            return
        try:
            db = self.get_db()
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                """
                INSERT OR REPLACE INTO enrichment_cache
                  (cache_key, name, city, state, phone, website, email, source, attempted_at, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key, name, city, state,
                    result.phone, result.website, result.email, result.source,
                    now, 1 if result.success else 0,
                ),
            )
            if not result.success:
                db.execute(
                    """
                    INSERT INTO enrichment_failures
                      (cache_key, name, city, state, attempt_count, last_attempted, errors, next_retry_after)
                    VALUES (?, ?, ?, ?, 1, ?, ?, NULL)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        attempt_count = attempt_count + 1,
                        last_attempted = excluded.last_attempted,
                        errors = excluded.errors,
                        next_retry_after = CASE
                            WHEN attempt_count + 1 >= 4
                            THEN datetime('now', '+30 days')
                            ELSE NULL
                        END
                    """,
                    (cache_key, name, city, state, now, "; ".join(result.errors)[:500]),
                )
            db.commit()
        except Exception as e:
            logger.warning("V227: cache write failed: %s", e)

    # ---- source 1: DuckDuckGo HTML search ---------------------------------

    @ddg_breaker
    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(2),
        retry=retry_if_exception_type(
            (httpx.HTTPError, httpx.TimeoutException, httpx.RemoteProtocolError)
        ),
    )
    def _search_ddg(self, name: str, city: str, state: str) -> dict | None:
        """POST the DDG HTML endpoint. Returns `{phone, website}` or None.

        Circuit-breakered at 5 consecutive failures (5 min reset). Retried
        twice with exponential backoff for transient HTTP errors.
        """
        query = f'"{name}" {city} {state} contractor phone'
        resp = self.client.post(DDG_URL, data={"q": query})
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError("DDG non-200", request=resp.request, response=resp)
        text = resp.text
        # Unwrap DDG's redirector: /l/?kh=-1&uddg=<encoded-url>
        text = re.sub(
            r"/l/\?kh=-1&uddg=([^&\"\'<>\s]+)",
            lambda m: "→" + urllib.parse.unquote(m.group(1)),
            text,
        )
        tree = HTMLParser(text)

        phone = _first_phone(tree.text(separator=" "))
        website = None
        for node in tree.css("a.result__url, a[href]"):
            href = node.attributes.get("href", "") or ""
            href = _html.unescape(href)
            if not href.startswith("http"):
                continue
            host = re.sub(r"https?://", "", href).split("/", 1)[0].lower().lstrip("www.")
            if any(host == d or host.endswith("." + d) for d in DIRECTORY_DOMAINS):
                continue
            website = href.rstrip(".,);")
            break

        if phone or website:
            return {"phone": phone, "website": website}
        return None

    # ---- source 2: candidate website scrape -------------------------------

    @website_scrape_breaker
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(2),
        retry=retry_if_exception_type(
            (httpx.HTTPError, httpx.TimeoutException, httpx.RemoteProtocolError)
        ),
    )
    def _scrape_website(self, base_url: str) -> dict | None:
        """Fetch homepage and up to 4 contact/about paths. Return {phone,email}."""
        found = {"phone": None, "email": None}
        base = base_url.rstrip("/")
        paths = [base] + [base + p for p in ("/contact", "/contact-us", "/about", "/about-us")]
        for url in paths:
            try:
                resp = self.client.get(url)
            except httpx.RequestError:
                continue
            if resp.status_code != 200 or not resp.text:
                continue
            tree = HTMLParser(resp.text)
            flat = tree.text(separator=" ")

            if not found["phone"]:
                found["phone"] = _first_phone(flat)
            if not found["email"]:
                # mailto: links are the most reliable source
                for a in tree.css('a[href^="mailto:"]'):
                    raw = (a.attributes.get("href") or "").replace("mailto:", "").split("?", 1)[0].strip()
                    if raw and "@" in raw:
                        domain = raw.split("@", 1)[-1].lower()
                        if domain not in JUNK_EMAIL_DOMAINS:
                            found["email"] = raw
                            break
                if not found["email"]:
                    found["email"] = _first_email(flat)
            if found["phone"] and found["email"]:
                break
        return found if (found["phone"] or found["email"]) else None

    # ---- orchestration -----------------------------------------------------

    def enrich_profile(self, name: str, city: str, state: str) -> EnrichmentResult:
        """Full enrichment — run DDG, then (if we got a website) scrape it."""
        result = EnrichmentResult()
        key = _cache_key(name, city or "", state or "")

        if self._is_cached(key):
            result.errors.append("cached_recently")
            return result
        if self._is_exhausted(key):
            result.errors.append("exhausted_retries")
            return result

        # Stage 1: DDG
        try:
            result.attempted_sources.append("duckduckgo")
            ddg = self._search_ddg(name, city or "", state or "")
            if ddg:
                result.phone = ddg.get("phone")
                result.website = ddg.get("website")
                if result.phone or result.website:
                    result.source = "duckduckgo"
        except pybreaker.CircuitBreakerError:
            result.errors.append("ddg_circuit_open")
        except Exception as e:
            result.errors.append(f"ddg_error: {str(e)[:80]}")

        time.sleep(1.5)

        # Stage 2: website scrape (only if DDG gave us a website)
        if result.website:
            try:
                result.attempted_sources.append("website_scrape")
                scrape = self._scrape_website(result.website)
                if scrape:
                    if scrape.get("phone"):
                        # Prefer the phone from the contractor's own website
                        result.phone = scrape["phone"]
                        result.source = "website_scrape"
                    if scrape.get("email"):
                        result.email = scrape["email"]
                        if result.source != "website_scrape":
                            result.source = "website_scrape"
            except pybreaker.CircuitBreakerError:
                result.errors.append("scrape_circuit_open")
            except Exception as e:
                result.errors.append(f"scrape_error: {str(e)[:80]}")

        self._save_result(key, name, city or "", state or "", result)
        return result

    def enrich_one(self, name: str, city: str, state: str) -> tuple[str | None, str | None]:
        """Backwards-compatible wrapper matching FreeEnrichmentEngine.enrich_one."""
        r = self.enrich_profile(name, city, state)
        return r.phone, r.website
