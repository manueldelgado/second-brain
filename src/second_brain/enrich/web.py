"""Fetch and extract full article content + metadata from a source URL.

Web clippers (e.g. Obsidian Web Clipper) sometimes capture only a stub — a
highlight, a title, or a link — instead of the full article body. This module
re-fetches the original URL and extracts clean markdown so the inbox pipeline
can recover the full text instead of committing the stub.

Key detail: many sites serve a JavaScript shell (no article text) to bot
User-Agents but the full HTML to browsers. We therefore download with a
browser User-Agent; without it, extraction frequently returns nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import trafilatura
from trafilatura.settings import use_config

logger = logging.getLogger(__name__)

# A real browser UA — sites like rachelandrew.co.uk return a 14 KB JS shell to
# the default trafilatura UA but the full 70 KB article to a browser UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Query-string keys that carry no meaning for the destination page and only
# exist for tracking/analytics. Stripped from stored source URLs.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset(
    {
        "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "igshid", "mc_cid",
        "mc_eid", "_hsenc", "_hsmi", "mkt_tok", "vero_id", "vero_conv",
        "oly_anon_id", "oly_enc_id", "ref", "ref_src", "ref_url", "spm",
    }
)

_config = None


def _get_config(timeout_seconds: int):
    """Build (once) a trafilatura config with a browser UA and download timeout."""
    global _config
    if _config is None:
        cfg = use_config()
        cfg.set("DEFAULT", "USER_AGENTS", _BROWSER_UA)
        cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(timeout_seconds))
        # Do not skip pages that lack a detectable date/author.
        cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
        _config = cfg
    return _config


def clean_url(url: str) -> str:
    """Strip tracking query params (utm_*, fbclid, …) while keeping the URL valid.

    Returns the URL unchanged if it has no query string or cannot be parsed.
    """
    if not url:
        return url
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    if not parts.query:
        return url

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    new_query = urlencode(kept)
    return urlunparse(parts._replace(query=new_query))


@dataclass
class WebArticle:
    """Result of fetching and extracting a source URL."""

    text: str | None = None
    title: str | None = None
    author: str | None = None
    date: str | None = None  # ISO date string (YYYY-MM-DD) or None
    canonical_url: str | None = None


def fetch_article(url: str, timeout_seconds: int = 20) -> WebArticle | None:
    """Download *url* and extract clean markdown + metadata.

    Returns None when the page cannot be downloaded at all. Returns a
    WebArticle with ``text=None`` when the page downloads but no article body
    can be extracted (e.g. a pure client-side-rendered app). Never raises —
    network and parsing failures are logged and folded into the return value so
    the caller can safely fall back to the captured content.
    """
    if not url:
        return None

    cfg = _get_config(timeout_seconds)

    try:
        downloaded = trafilatura.fetch_url(url, config=cfg)
    except Exception as exc:  # pragma: no cover - network variance
        logger.warning("Could not download %s: %s", url, exc)
        return None

    if not downloaded:
        logger.info("No content downloaded from %s (empty response)", url)
        return None

    text: str | None = None
    try:
        text = trafilatura.extract(
            downloaded,
            output_format="markdown",
            include_links=True,
            include_comments=False,
            favor_recall=True,
            config=cfg,
        )
    except Exception as exc:  # pragma: no cover - parsing variance
        logger.warning("Extraction failed for %s: %s", url, exc)

    author = date = title = canonical = None
    try:
        meta = trafilatura.extract_metadata(downloaded)
        if meta is not None:
            author = getattr(meta, "author", None)
            date = getattr(meta, "date", None)
            title = getattr(meta, "title", None)
            canonical = getattr(meta, "url", None)
    except Exception as exc:  # pragma: no cover - parsing variance
        logger.debug("Metadata extraction failed for %s: %s", url, exc)

    return WebArticle(
        text=text.strip() if text else None,
        title=title or None,
        author=author or None,
        date=date or None,
        canonical_url=canonical or None,
    )
