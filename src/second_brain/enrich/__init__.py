"""Content enrichment — recover full article text and metadata from source URLs."""

from second_brain.enrich.web import WebArticle, clean_url, fetch_article

__all__ = ["WebArticle", "clean_url", "fetch_article"]
