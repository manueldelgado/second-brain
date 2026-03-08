"""Inbox classification pipeline — scan 00 Inbox/, classify, and move to 01 Notes/."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from second_brain.config import Settings, TaxonomyConfig
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

    for item in items:
        report.items_processed += 1
        try:
            analysis = llm.analyze_content(item.content, taxonomy)
            processed = _write_inbox_item(
                item=item,
                analysis=analysis,
                vault=vault,
                settings=settings,
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

    # Build custom_ids that are safe for the Anthropic API
    custom_ids = [f"ib{i:04d}" for i in range(len(items))]

    requests = [
        BatchRequest(
            custom_id=cid,
            content=item.content,
            taxonomy=taxonomy,
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
    dry_run: bool,
) -> bool:
    """Classify and move a single inbox item. Returns True if classified."""
    if not analysis.tags:
        logger.warning("  LLM returned no tags for '%s', leaving in inbox", item.title)
        return False

    is_pdf = item.metadata.get("is_pdf", False)
    if is_pdf:
        return _process_pdf_item(item, analysis, vault, settings, dry_run)

    existing_fm = item.metadata.get("existing_frontmatter", {})
    fm = _build_frontmatter(item, analysis, existing_fm)

    content_type = analysis.content_type
    template_name = TEMPLATE_MAP.get(content_type, "clipping.md.j2")
    rendered = render_note(template_name, fm, analysis, item.content)

    original_path = Path(item.metadata["original_path"])
    filename = sanitize_filename(fm.title)

    if dry_run:
        logger.info(
            "  [DRY RUN] Would classify '%s' as %s → %s",
            item.title,
            content_type,
            filename,
        )
        return True

    vault.create_note(original_path.parent.name, original_path.name, rendered)
    vault.move_note(original_path, settings.vault.notes_folder)
    logger.info("  Classified: %s → %s/%s", item.title, settings.vault.notes_folder, filename)
    return True


def _process_pdf_item(
    item: IngestItem,
    analysis: ContentAnalysis,
    vault: VaultBackend,
    settings: Settings,
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
        source="",
        author=[],
        created=date.today(),
        type=analysis.content_type if analysis.content_type in ("paper", "book") else "paper",
        status="classified",
        tags=analysis.tags,
        description=analysis.description,
    )
    template_name = TEMPLATE_MAP.get(fm.type, "paper.md.j2")
    rendered = render_note(template_name, fm, analysis, "", extra={"pdf_filename": pdf_filename})

    note_filename = sanitize_filename(item.title)
    vault.create_note(settings.vault.notes_folder, note_filename, rendered)
    original_path.unlink()

    logger.info("  PDF processed: %s → wrapper note + asset", pdf_filename)
    return True


def _build_frontmatter(
    item: IngestItem,
    analysis: ContentAnalysis,
    existing: dict,
) -> NoteFrontmatter:
    return NoteFrontmatter(
        title=existing.get("title", item.title),
        source=existing.get("source", item.source_url),
        author=existing.get("author", item.author) or [],
        created=existing.get("created", date.today()),
        type=existing.get("type", analysis.content_type),
        status="classified",
        tags=analysis.tags,
        description=analysis.description,
        newsletter=existing.get("newsletter"),
        published=existing.get("published", item.published),
        rating=existing.get("rating"),
        journal=existing.get("journal"),
        doi=existing.get("doi"),
        year=existing.get("year"),
        isbn=existing.get("isbn"),
    )
