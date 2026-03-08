"""Newsletter ingestion pipeline — fetch emails from Gmail, create classified notes."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from second_brain.config import NewslettersConfig, Settings, TaxonomyConfig
from second_brain.gmail.client import GmailClient  # also used as type in finalize_newsletter_batch
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
from second_brain.vault.base import VaultBackend
from second_brain.vault.sync_state import SyncState

logger = logging.getLogger(__name__)

NEWSLETTER_LABEL = "Newsletters"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_newsletter_pipeline(
    settings: Settings,
    newsletters: NewslettersConfig,
    taxonomy: TaxonomyConfig,
    vault: VaultBackend,
    gmail: GmailClient,
    llm: LLMProvider,
    sync_state: SyncState,
    dry_run: bool = False,
    batch_provider: BatchLLMProvider | None = None,
    batch_state: BatchStateManager | None = None,
    no_wait: bool = False,
) -> PipelineReport:
    """Run the full newsletter ingestion pipeline.

    When *batch_provider* is supplied the pipeline operates in batch mode:
    all items are collected first, submitted to the provider in one call, and
    then either waited on inline or handed off to ``second-brain resume-batch``
    (when *no_wait* is True).  Without *batch_provider* the original
    synchronous item-by-item behaviour is used.
    """
    if batch_provider is not None:
        return _run_batch(
            settings=settings,
            newsletters=newsletters,
            taxonomy=taxonomy,
            vault=vault,
            gmail=gmail,
            batch_provider=batch_provider,
            batch_state=batch_state,
            sync_state=sync_state,
            dry_run=dry_run,
            no_wait=no_wait,
        )
    return _run_sync(
        settings=settings,
        newsletters=newsletters,
        taxonomy=taxonomy,
        vault=vault,
        gmail=gmail,
        llm=llm,
        sync_state=sync_state,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Sync execution path (original behaviour)
# ---------------------------------------------------------------------------

def _run_sync(
    settings: Settings,
    newsletters: NewslettersConfig,
    taxonomy: TaxonomyConfig,
    vault: VaultBackend,
    gmail: GmailClient,
    llm: LLMProvider,
    sync_state: SyncState,
    dry_run: bool,
) -> PipelineReport:
    report = PipelineReport(pipeline_name="newsletters")
    label_id = gmail.get_or_create_label(NEWSLETTER_LABEL) if not dry_run else None

    for source in newsletters.sources:
        after_date, min_internal_date = _compute_after_date(sync_state, source.name, settings)
        logger.info("Fetching %s (after %s)", source.name, after_date)

        try:
            items = gmail.fetch_newsletters(source.email, source.name, after_date, min_internal_date)
        except Exception as e:
            report.errors.append(f"Gmail fetch failed for {source.name}: {e}")
            continue

        if not items:
            logger.info("  No new emails for %s", source.name)
            continue

        source_created = 0
        for item in items:
            report.items_processed += 1
            try:
                analysis = llm.analyze_content(item.content, taxonomy, content_hint=item.newsletter_name)
                _write_newsletter_note(
                    item=item,
                    analysis=analysis,
                    vault=vault,
                    settings=settings,
                    sync_state=sync_state,
                    dry_run=dry_run,
                )
                _apply_label_safe(gmail, item, label_id)
                report.items_created += 1
                source_created += 1
            except Exception as e:
                report.errors.append(f"[{source.name}] {item.title}: {e}")
                logger.exception("Failed to process: %s", item.title)

        logger.info("  Created %d notes for %s", source_created, source.name)

    if not dry_run:
        sync_state.update_global_last_run()

    report.log_summary()
    report.print_summary()
    return report


# ---------------------------------------------------------------------------
# Batch execution path
# ---------------------------------------------------------------------------

def _run_batch(
    settings: Settings,
    newsletters: NewslettersConfig,
    taxonomy: TaxonomyConfig,
    vault: VaultBackend,
    gmail: GmailClient,
    batch_provider: BatchLLMProvider,
    batch_state: BatchStateManager | None,
    sync_state: SyncState,
    dry_run: bool,
    no_wait: bool,
) -> PipelineReport:
    report = PipelineReport(pipeline_name="newsletters (batch)")

    # Phase 1 — collect all items across all sources
    all_items: list[IngestItem] = []
    for source in newsletters.sources:
        after_date, min_internal_date = _compute_after_date(sync_state, source.name, settings)
        logger.info("Fetching %s (after %s)", source.name, after_date)
        try:
            items = gmail.fetch_newsletters(source.email, source.name, after_date, min_internal_date)
        except Exception as e:
            report.errors.append(f"Gmail fetch failed for {source.name}: {e}")
            continue
        if items:
            all_items.extend(items)
            logger.info("  Collected %d items from %s", len(items), source.name)
        else:
            logger.info("  No new emails for %s", source.name)

    if not all_items:
        logger.info("No items to process.")
        report.log_summary()
        report.print_summary()
        return report

    report.items_processed = len(all_items)

    if dry_run:
        for item in all_items:
            logger.info("  [DRY RUN] Would submit for batch: %s", item.title)
        report.log_summary()
        report.print_summary()
        return report

    # Phase 2 — build and submit batch
    requests = [
        BatchRequest(
            custom_id=f"item{i:04d}",
            content=item.content,
            taxonomy=taxonomy,
            content_hint=item.newsletter_name,
        )
        for i, item in enumerate(all_items)
    ]
    batch_id = batch_provider.submit_batch(requests)

    # Phase 3 — persist state (always, in case we crash before finalizing)
    pending_items = [
        PendingBatchItem(custom_id=f"item{i:04d}", item=item)
        for i, item in enumerate(all_items)
    ]
    pending = PendingBatch(
        batch_id=batch_id,
        pipeline="newsletters",
        submitted_at=datetime.now(timezone.utc),
        items=pending_items,
    )
    if batch_state is not None:
        batch_state.add_batch(pending)

    if no_wait:
        logger.info(
            "Batch %s submitted (%d items). Run `second-brain resume-batch` to finalize.",
            batch_id,
            len(all_items),
        )
        report.log_summary()
        report.print_summary()
        return report

    # Phase 4 — poll until complete
    _poll_until_complete(
        provider=batch_provider,
        batch_id=batch_id,
        poll_interval=settings.llm.batch.poll_interval_seconds,
        timeout_hours=settings.llm.batch.timeout_hours,
    )

    # Phase 5 — finalize
    results = batch_provider.get_batch_results(batch_id)
    created, errors = finalize_newsletter_batch(
        results=results,
        pending=pending,
        vault=vault,
        settings=settings,
        sync_state=sync_state,
        dry_run=dry_run,
    )
    report.items_created = created
    report.errors.extend(errors)

    if batch_state is not None:
        batch_state.remove_batch(batch_id)

    if not dry_run:
        sync_state.update_global_last_run()

    report.log_summary()
    report.print_summary()
    return report


# ---------------------------------------------------------------------------
# Finalization (used by both inline batch and resume-batch)
# ---------------------------------------------------------------------------

def finalize_newsletter_batch(
    results: list[BatchResult],
    pending: PendingBatch,
    vault: VaultBackend,
    settings: Settings,
    sync_state: SyncState,
    dry_run: bool,
    gmail: GmailClient | None = None,
) -> tuple[int, list[str]]:
    """Write vault notes for a completed batch. Returns (created_count, errors)."""
    result_map = {r.custom_id: r for r in results}
    label_id = gmail.get_or_create_label(NEWSLETTER_LABEL) if gmail and not dry_run else None
    created = 0
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
            _write_newsletter_note(
                item=pending_item.item,
                analysis=result.analysis,
                vault=vault,
                settings=settings,
                sync_state=sync_state,
                dry_run=dry_run,
            )
            _apply_label_safe(gmail, pending_item.item, label_id)
            created += 1
        except Exception as exc:
            errors.append(f"{pending_item.item.title}: {exc}")
            logger.exception("Failed to write note for %s", pending_item.item.title)

    return created, errors


# ---------------------------------------------------------------------------
# Shared write step
# ---------------------------------------------------------------------------

def _write_newsletter_note(
    item: IngestItem,
    analysis: ContentAnalysis,
    vault: VaultBackend,
    settings: Settings,
    sync_state: SyncState,
    dry_run: bool,
) -> None:
    """Render and persist a single newsletter note (sync and batch share this)."""
    message_id = item.metadata.get("message_id")
    gmail_url = f"https://mail.google.com/mail/u/0/#all/{message_id}" if message_id else None
    fm = NoteFrontmatter(
        title=item.title,
        source=item.source_url,
        author=item.author,
        created=date.today(),
        type="newsletter",
        status="classified",
        tags=analysis.tags,
        description=analysis.description,
        newsletter=item.newsletter_name,
        published=item.published,
        gmail_url=gmail_url,
    )
    template_name = TEMPLATE_MAP["newsletter"]
    rendered = render_note(template_name, fm, analysis, item.content)
    filename = sanitize_filename(item.title)

    if dry_run:
        logger.info("  [DRY RUN] Would create: %s", filename)
        return

    vault.create_note(settings.vault.notes_folder, filename, rendered)
    logger.info("  Created: %s", filename)

    internal_date_iso = item.metadata.get("internal_date_iso")
    if internal_date_iso:
        ts = datetime.fromisoformat(internal_date_iso)
        sync_state.update_sync(item.newsletter_name, ts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_label_safe(
    gmail: GmailClient | None,
    item: IngestItem,
    label_id: str | None,
) -> None:
    """Apply the newsletter label to the source email; log and continue on failure."""
    if gmail is None or label_id is None:
        return
    message_id = item.metadata.get("message_id")
    if not message_id:
        return
    try:
        gmail.apply_label(message_id, label_id)
    except Exception as exc:
        logger.warning("Could not apply label to message %s: %s", message_id, exc)


def _compute_after_date(
    sync_state: SyncState,
    newsletter_name: str,
    settings: Settings,
) -> tuple[datetime, datetime | None]:
    """Return (after_date, min_internal_date).

    *after_date* is converted to a calendar date for the coarse Gmail query.
    *min_internal_date* is the precise timestamp used for client-side filtering;
    it is None for the fallback path so all emails in the lookback window are
    accepted on the first run.
    """
    last_sync = sync_state.get_last_sync(newsletter_name)
    if last_sync:
        return last_sync, last_sync

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.processing.default_lookback_days)
    return cutoff, None


def _poll_until_complete(
    provider: BatchLLMProvider,
    batch_id: str,
    poll_interval: int,
    timeout_hours: int,
) -> None:
    deadline = time.monotonic() + timeout_hours * 3600
    while time.monotonic() < deadline:
        status = provider.get_batch_status(batch_id)
        logger.info(
            "Batch %s: %s (%d/%d succeeded, %d failed)",
            batch_id,
            status.state,
            status.succeeded,
            status.total,
            status.failed,
        )
        if status.is_terminal:
            if status.state != "complete":
                raise RuntimeError(f"Batch {batch_id} ended with state '{status.state}'")
            return
        time.sleep(poll_interval)
    raise TimeoutError(
        f"Batch {batch_id} did not complete within {timeout_hours}h. "
        f"Run `second-brain resume-batch` to finalize it later."
    )
