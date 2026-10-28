# What: downloads a job page safely and extracts its main text with trafilatura.
# Why: users often paste a link instead of the text. Every URL (including each redirect) is
#      checked by the URL guard, and downloads have a time and size limit (§9).
# Used by: app/agent/nodes/ingest.py (ingest_job), later the fetch_job_posting tool.

from urllib.parse import urljoin

import httpx
import trafilatura

from app.guards.url_guard import validate_url
from app.utils.errors import FetchError

TIMEOUT_SECONDS = 10
MAX_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_REDIRECTS = 5
_HEADERS = {"User-Agent": "JobApplicationCopilot/0.1 (+personal job-search assistant)"}


def _download(url: str) -> str:
    """Return the HTML of `url`, following redirects only to URLs that pass the guard."""
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, headers=_HEADERS) as client:
        for _ in range(MAX_REDIRECTS + 1):
            validate_url(url)
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                body = b""
                for part in response.iter_bytes():
                    body += part
                    if len(body) > MAX_BYTES:
                        raise FetchError(f"Page larger than {MAX_BYTES} bytes: {url}")
                return body.decode(response.encoding or "utf-8", errors="replace")
    raise FetchError(f"Too many redirects: {url}")


def fetch_page_text(url: str) -> str:
    """Download a web page and return its main text (no menus, ads or footers).

    Raises:
        GuardBlockedError: the URL (or a redirect) points somewhere unsafe.
        FetchError: download failed, timed out, was too large, or had no readable text
            (for example a login page).
    """
    try:
        html = _download(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"Download failed for {url}: {exc}") from exc

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    if not text or not text.strip():
        raise FetchError(f"No readable text at {url}")
    return text
