"""Inbox classification pipeline — scan 00 Inbox/, classify, and move to 01 Notes/."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from second_brain.config import Settings, TaxonomyConfig
from second_brain.enrich import clean_url, fetch_article
from second_brain.llm.base import LLMProvider
from second_brain.llm.batch import BatchLLMProvider, BatchRequest, BatchResult
from second_brain.models import ContentAnalysis, IngestItem, NoteFrontmatter
from second_brain.pipeline.base import (
    TEMPLATE_MAP,
    PipelineReport,
    render_note,
    sanitize_filename,
)
from second_brain.pipeline.batch_state import BatchStateManager, PendingBatch, PendingBatchItem
from second_brain.pipeline.newsletter import _poll_until_complete
from second_brain.vault.base import VaultBackend
from second_brain.vault.scanner import scan_inbox

logger = logging.getLogger(__name__)

# Metadata keys used to pass web-enrichment results from the collect phase to
# the write phase (survives YAML serialization in batch mode).
_WEB_AUTHOR_KEY = "web_author"
_WEB_DATE_KEY = "web_date"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_inbox_pipeline(
    settings: Settings,
    taxonomy: TaxonomyConfig,
    vault: VaultBackend,
    llm: LLMProvider,
    dry_run: bool = False,
    batch_provider: BatchLLMProvider | None = None,
    batch_state: BatchStateManager | None = None,
    no_wait: bool = False,
) -> PipelineReport:
    """Run the inbox classification pipeline.

    When *batch_provider* is supplied all inbox items are submitted in one batch
    call instead of being processed one-by-one.
    """
    if batch_provider is not None:
        return _run_batch(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            batch_provider=batch_provider,
            batch_state=batch_state,
            dry_run=dry_run,
            no_wait=no_wait,
        )
    return _run_sync(
        settings=settings,
        taxonomy=taxonomy,
        vault=vault,
        llm=llm,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Sync execution path (original behaviour)
# ---------------------------------------------------------------------------

def _run_sync(
    settings: Settings,
    taxonomy: TaxonomyConfig,
    vault: VaultBackend,
    llm: LLMProvider,
    dry_run: bool,
) -> PipelineReport:
    report = PipelineReport(pipeline_name="inbox")

    items = scan_inbox(vault, settings.vault.inbox_folder)
    logger.info("Found %d items in inbox", len(items))

    _enrich_items(items, settings, dry_run)

    for item in items:
        report.items_processed += 1
        try:
            analysis = llm.analyze_content(
                item.content, taxonomy, content_hint=_hint_for(item)
            )
            processed = _write_inbox_item(
                item=item,
                analysis=analysis,
                vault=vault,
                settings=settings,
                taxonomy=taxonomy,
                dry_run=dry_run,
            )
            if processed:
                report.items_created += 1
            else:
                report.items_skipped += 1
        except Exception as e:
            report.errors.append(f"{item.title}: {e}")
            logger.exception("Failed to process inbox item: %s", item.title)

    report.log_summary()
    report.print_summary()
    return report


# ---------------------------------------------------------------------------
# Batch execution path
# ---------------------------------------------------------------------------

def _run_batch(
    settings: Settings,
    taxonomy: TaxonomyConfig,
    vault: VaultBackend,
    batch_provider: BatchLLMProvider,
    batch_state: BatchStateManager | None,
    dry_run: bool,
    no_wait: bool,
) -> PipelineReport:
    report = PipelineReport(pipeline_name="inbox (batch)")

    items = scan_inbox(vault, settings.vault.inbox_folder)
    logger.info("Found %d items in inbox", len(items))

    if not items:
        report.log_summary()
        report.print_summary()
        return report

    report.items_processed = len(items)

    if dry_run:
        for item in items:
            logger.info("  [DRY RUN] Would submit for batch: %s", item.title)
        report.log_summary()
        report.print_summary()
        return report

    # Enrich before submitting so the LLM sees the recovered full text and the
    # enriched item (content + metadata) is persisted in batch_state.
    _enrich_items(items, settings, dry_run)

    # Build custom_ids that are safe for the Anthropic API
    custom_ids = [f"ib{i:04d}" for i in range(len(items))]

    requests = [
        BatchRequest(
            custom_id=cid,
            content=item.content,
            taxonomy=taxonomy,
            content_hint=_hint_for(item),
        )
        for cid, item in zip(custom_ids, items)
    ]
    batch_id = batch_provider.submit_batch(requests)

    pending_items = [
        PendingBatchItem(custom_id=cid, item=item)
        for cid, item in zip(custom_ids, items)
    ]
    pending = PendingBatch(
        batch_id=batch_id,
        pipeline="inbox",
        submitted_at=datetime.now(timezone.utc),
        items=pending_items,
    )
    if batch_state is not None:
        batch_state.add_batch(pending)

    if no_wait:
        logger.info(
            "Batch %s submitted (%d items). Run `second-brain resume-batch` to finalize.",
            batch_id,
            len(items),
        )
        report.log_summary()
        report.print_summary()
        return report

    _poll_until_complete(
        provider=batch_provider,
        batch_id=batch_id,
        poll_interval=settings.llm.batch.poll_interval_seconds,
        timeout_hours=settings.llm.batch.timeout_hours,
    )

    results = batch_provider.get_batch_results(batch_id)
    created, skipped, errors = finalize_inbox_batch(
        results=results,
        pending=pending,
        vault=vault,
        settings=settings,
        taxonomy=taxonomy,
        dry_run=dry_run,
    )
    report.items_created = created
    report.items_skipped = skipped
    report.errors.extend(errors)

    if batch_state is not None:
        batch_state.remove_batch(batch_id)

    report.log_summary()
    report.print_summary()
    return report


# ---------------------------------------------------------------------------
# Finalization (used by both inline batch and resume-batch)
# ---------------------------------------------------------------------------

def finalize_inbox_batch(
    results: list[BatchResult],
    pending: PendingBatch,
    vault: VaultBackend,
    settings: Settings,
    taxonomy: TaxonomyConfig,
    dry_run: bool,
) -> tuple[int, int, list[str]]:
    """Write vault notes for a completed inbox batch.

    Returns (created_count, skipped_count, errors).
    """
    result_map = {r.custom_id: r for r in results}
    created = 0
    skipped = 0
    errors: list[str] = []

    for pending_item in pending.items:
        result = result_map.get(pending_item.custom_id)
        if result is None:
            errors.append(f"{pending_item.item.title}: no result returned by provider")
            continue
        if result.analysis is None:
            errors.append(f"{pending_item.item.title}: {result.error}")
            continue
        try:
            processed = _write_inbox_item(
                item=pending_item.item,
                analysis=result.analysis,
                vault=vault,
                settings=settings,
                taxonomy=taxonomy,
                dry_run=dry_run,
            )
            if processed:
                created += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"{pending_item.item.title}: {exc}")
            logger.exception("Failed to write inbox note for %s", pending_item.item.title)

    return created, skipped, errors


# ---------------------------------------------------------------------------
# Shared write step
# ---------------------------------------------------------------------------

def _write_inbox_item(
    item: IngestItem,
    analysis: ContentAnalysis,
    vault: VaultBackend,
    settings: Settings,
    taxonomy: TaxonomyConfig,
    dry_run: bool,
) -> bool:
    """Classify and move a single inbox item. Always writes an enriched note.

    Items the LLM could not tag are still written (with ``status: needs-tags``)
    so their recovered content, URL and summary are never lost — they are moved
    to Notes for manual tagging rather than abandoned in the inbox.
    """
    existing_fm = item.metadata.get("existing_frontmatter", {})
    tags, classified = _resolve_tags(existing_fm.get("tags"), analysis.tags, taxonomy)
    status = "classified" if classified else "needs-tags"
    if not classified:
        logger.info(
            "  No valid taxonomy tag for '%s' — writing with status: needs-tags",
            item.title,
        )

    if item.metadata.get("is_pdf", False):
        return _process_pdf_item(item, analysis, vault, settings, tags, status, dry_run)

    fm = _build_frontmatter(item, analysis, existing_fm, tags=tags, status=status)

    template_name = TEMPLATE_MAP.get(fm.type, "clipping.md.j2")
    rendered = render_note(template_name, fm, analysis, item.content)

    original_path = Path(item.metadata["original_path"])
    filename = sanitize_filename(fm.title)

    if dry_run:
        logger.info(
            "  [DRY RUN] Would classify '%s' as %s (%s) → %s",
            item.title,
            fm.type,
            status,
            filename,
        )
        return True

    vault.create_note(original_path.parent.name, original_path.name, rendered)
    vault.move_note(original_path, settings.vault.notes_folder)
    logger.info(
        "  Classified: %s → %s/%s (%s)",
        item.title,
        settings.vault.notes_folder,
        filename,
        status,
    )
    return True


def _process_pdf_item(
    item: IngestItem,
    analysis: ContentAnalysis,
    vault: VaultBackend,
    settings: Settings,
    tags: list[str],
    status: str,
    dry_run: bool,
) -> bool:
    """Copy PDF to assets and create a wrapper note."""
    original_path = Path(item.metadata["original_path"])
    pdf_filename = original_path.name

    if dry_run:
        logger.info("  [DRY RUN] Would process PDF: %s", pdf_filename)
        return True

    vault.copy_asset(original_path, settings.vault.assets_folder)

    fm = NoteFrontmatter(
        title=item.title,
        source=item.source_url,
        author=_resolve_author(None, item),
        created=date.today(),
        type=analysis.content_type if analysis.content_type in ("paper", "book") else "paper",
        status=status,
        tags=tags,
        description=analysis.description,
    )
    template_name = TEMPLATE_MAP.get(fm.type, "paper.md.j2")
    rendered = render_note(template_name, fm, analysis, "", extra={"pdf_filename": pdf_filename})

    note_filename = sanitize_filename(item.title)
    vault.create_note(settings.vault.notes_folder, note_filename, rendered)
    original_path.unlink()

    logger.info("  PDF processed: %s → wrapper note + asset (%s)", pdf_filename, status)
    return True


def _build_frontmatter(
    item: IngestItem,
    analysis: ContentAnalysis,
    existing: dict,
    *,
    tags: list[str],
    status: str,
) -> NoteFrontmatter:
    return NoteFrontmatter(
        title=existing.get("title") or item.title,
        # item.source_url is the multi-key, tracking-stripped, canonical URL.
        source=item.source_url or existing.get("source") or "",
        author=_resolve_author(existing.get("author"), item),
        created=_sane_date(existing.get("created")) or date.today(),
        type=existing.get("type") or analysis.content_type,
        status=status,
        tags=tags,
        description=analysis.description or existing.get("description", ""),
        newsletter=existing.get("newsletter"),
        published=_resolve_published(existing.get("published"), item),
        rating=existing.get("rating"),
        journal=existing.get("journal"),
        doi=existing.get("doi"),
        year=existing.get("year"),
        isbn=existing.get("isbn"),
    )


# ---------------------------------------------------------------------------
# Enrichment + metadata helpers
# ---------------------------------------------------------------------------

def _enrich_items(items: list[IngestItem], settings: Settings, dry_run: bool) -> None:
    """Recover full article text + metadata from each item's source URL, in place.

    Content is never shrunk: a fetched body replaces the captured one only when
    it is longer (protects paywalled captures made in a logged-in browser, where
    an anonymous re-fetch would return less). Fetch failures are non-fatal — the
    captured content is kept. The source URL is always normalized (tracking
    params stripped) even when the fetch fails.
    """
    if not settings.processing.enrich_from_web:
        return

    timeout = settings.processing.web_fetch_timeout_seconds
    for item in items:
        if item.metadata.get("is_pdf"):
            continue
        url = item.source_url
        if not url:
            continue

        item.source_url = clean_url(url)  # keep a clean URL even if the fetch fails

        if dry_run:
            logger.info("  [DRY RUN] Would fetch full article: %s", url)
            continue

        article = fetch_article(url, timeout_seconds=timeout)
        if article is None:
            logger.info("  Could not fetch %s — keeping captured content", url)
            continue

        captured_len = len(item.content or "")
        if article.text and len(article.text) > captured_len:
            logger.info(
                "  Recovered fuller article for '%s' (%d → %d chars)",
                item.title,
                captured_len,
                len(article.text),
            )
            item.content = article.text

        # NB: we deliberately do NOT adopt article.canonical_url — many sites
        # return the site root (og:url / rel=canonical pointing home), which
        # would replace the specific article URL the user clipped. The cleaned
        # original URL is always the most faithful source.
        if article.author:
            item.metadata[_WEB_AUTHOR_KEY] = article.author
        if article.date:
            item.metadata[_WEB_DATE_KEY] = article.date


def _hint_for(item: IngestItem) -> str | None:
    """Give the LLM the title + source domain as classification context."""
    parts: list[str] = []
    if item.title:
        parts.append(item.title)
    domain = _domain_of(item.source_url)
    if domain:
        parts.append(f"({domain})")
    return " ".join(parts) or None


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""


def _as_str_list(value: object) -> list[str]:
    """Normalize a scalar/list/None frontmatter value into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                out.append(s)
        return out
    return []


def _resolve_tags(
    existing: object,
    llm_tags: object,
    taxonomy: TaxonomyConfig,
) -> tuple[list[str], bool]:
    """Merge existing tags with taxonomy-valid LLM tags.

    Existing tags are kept verbatim (including clipper markers like
    ``clippings``). LLM tags are added only when they exist in the taxonomy.
    Returns ``(merged, classified)`` — *classified* is True when the LLM
    contributed at least one valid taxonomy tag.
    """
    merged = _as_str_list(existing)
    valid = taxonomy.all_valid_tags
    classified = False
    for tag in _as_str_list(llm_tags):
        if tag in valid:
            classified = True
            if tag not in merged:
                merged.append(tag)
        else:
            logger.info("  Dropping non-taxonomy tag suggested by LLM: %s", tag)
    return merged, classified


def _resolve_author(existing_author: object, item: IngestItem) -> list[str]:
    """Prefer an existing author; else the web-fetched author; else empty."""
    existing = _as_str_list(existing_author)
    if existing:
        return existing
    web_author = item.metadata.get(_WEB_AUTHOR_KEY)
    if web_author:
        return [f"[[{web_author}]]"]
    return []


def _resolve_published(existing_published: object, item: IngestItem) -> date | None:
    """Prefer a plausible existing date; else the web-fetched date; drop garbage."""
    for candidate in (
        existing_published,
        item.metadata.get(_WEB_DATE_KEY),
        item.published,
    ):
        parsed = _sane_date(candidate)
        if parsed is not None:
            return parsed
    return None


def _sane_date(value: object) -> date | None:
    """Parse *value* into a date, rejecting implausible years (clipper garbage)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except (ValueError, TypeError):
            return None
    if 2005 <= parsed.year <= date.today().year + 1:
        return parsed
    return None
