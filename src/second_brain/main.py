"""CLI entry point for the Second Brain automation."""

from __future__ import annotations

import logging
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv(override=True)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_all_config(config_dir: Path):
    from second_brain.config import load_newsletters, load_settings, load_taxonomy

    settings = load_settings(config_dir)
    newsletters = load_newsletters(config_dir)
    taxonomy = load_taxonomy(config_dir)
    return settings, newsletters, taxonomy


def _build_vault(settings):
    from second_brain.vault.filesystem import FilesystemBackend
    from second_brain.vault.obsidian_cli import ObsidianCLIBackend

    if settings.vault_backend == "obsidian_cli":
        return ObsidianCLIBackend(settings.vault.root)
    return FilesystemBackend(settings.vault.root)


def _build_llm(settings):
    from second_brain.llm.claude import ClaudeProvider

    return ClaudeProvider(model=settings.llm.model, max_tokens=settings.llm.max_tokens)


def _build_batch_provider(settings):
    from second_brain.llm.claude_batch import ClaudeBatchProvider

    return ClaudeBatchProvider(model=settings.llm.model, max_tokens=settings.llm.max_tokens)


def _build_gmail(settings):
    from second_brain.gmail.client import GmailClient

    return GmailClient(
        credentials_file=settings.gmail.credentials_file,
        token_file=settings.gmail.token_file,
        scopes=settings.gmail.scopes,
    )


def _build_sync_state(settings):
    from second_brain.vault.sync_state import SyncState

    return SyncState(Path(settings.vault.sync_state_file))


def _build_batch_state(settings):
    from second_brain.pipeline.batch_state import BatchStateManager

    return BatchStateManager(Path(settings.vault.batch_state_file))


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
@click.option("--config-dir", type=click.Path(exists=True, path_type=Path), default="config")
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def cli(ctx: click.Context, config_dir: Path, verbose: bool) -> None:
    """Second Brain automation — newsletter ingestion and inbox processing."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config_dir


# ---------------------------------------------------------------------------
# newsletters
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--dry-run", is_flag=True, help="Preview mode — no files written")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.option("--batch", is_flag=True, help="Use the LLM batch API instead of synchronous calls")
@click.option(
    "--no-wait",
    is_flag=True,
    help="With --batch: submit and exit; run `resume-batch` later to finalize",
)
@click.pass_context
def newsletters(ctx: click.Context, dry_run: bool, verbose: bool, batch: bool, no_wait: bool) -> None:
    """Run the newsletter ingestion pipeline."""
    if verbose:
        _setup_logging(True)

    from second_brain.pipeline.newsletter import run_newsletter_pipeline

    config_dir = ctx.obj["config_dir"]
    settings, nl_config, taxonomy = _load_all_config(config_dir)

    vault = _build_vault(settings)
    gmail = _build_gmail(settings)
    llm = _build_llm(settings)
    sync_state = _build_sync_state(settings)

    batch_provider = _build_batch_provider(settings) if batch else None
    batch_state = _build_batch_state(settings) if batch else None

    run_newsletter_pipeline(
        settings=settings,
        newsletters=nl_config,
        taxonomy=taxonomy,
        vault=vault,
        gmail=gmail,
        llm=llm,
        sync_state=sync_state,
        dry_run=dry_run or settings.processing.dry_run,
        batch_provider=batch_provider,
        batch_state=batch_state,
        no_wait=no_wait,
    )


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--dry-run", is_flag=True, help="Preview mode — no files written")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.option("--batch", is_flag=True, help="Use the LLM batch API instead of synchronous calls")
@click.option(
    "--no-wait",
    is_flag=True,
    help="With --batch: submit and exit; run `resume-batch` later to finalize",
)
@click.pass_context
def inbox(ctx: click.Context, dry_run: bool, verbose: bool, batch: bool, no_wait: bool) -> None:
    """Run the inbox classification pipeline."""
    if verbose:
        _setup_logging(True)

    from second_brain.pipeline.inbox import run_inbox_pipeline

    config_dir = ctx.obj["config_dir"]
    settings, _, taxonomy = _load_all_config(config_dir)

    vault = _build_vault(settings)
    llm = _build_llm(settings)

    batch_provider = _build_batch_provider(settings) if batch else None
    batch_state = _build_batch_state(settings) if batch else None

    run_inbox_pipeline(
        settings=settings,
        taxonomy=taxonomy,
        vault=vault,
        llm=llm,
        dry_run=dry_run or settings.processing.dry_run,
        batch_provider=batch_provider,
        batch_state=batch_state,
        no_wait=no_wait,
    )


# ---------------------------------------------------------------------------
# run (both pipelines)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--dry-run", is_flag=True, help="Preview mode — no files written")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.option("--batch", is_flag=True, help="Use the LLM batch API for both pipelines")
@click.option("--no-wait", is_flag=True, help="With --batch: submit and exit")
@click.pass_context
def run(ctx: click.Context, dry_run: bool, verbose: bool, batch: bool, no_wait: bool) -> None:
    """Run both pipelines (newsletters + inbox)."""
    if verbose:
        _setup_logging(True)
    ctx.invoke(newsletters, dry_run=dry_run, verbose=verbose, batch=batch, no_wait=no_wait)
    ctx.invoke(inbox, dry_run=dry_run, verbose=verbose, batch=batch, no_wait=no_wait)


# ---------------------------------------------------------------------------
# resume-batch
# ---------------------------------------------------------------------------

@cli.command("resume-batch")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def resume_batch(ctx: click.Context, verbose: bool) -> None:
    """Poll all pending batches and finalize any that are complete.

    Reads batch_state.yaml, checks each batch's status, and processes results
    for batches that have finished.  Can be run repeatedly — already-complete
    batches are removed from the state file after finalization.
    """
    if verbose:
        _setup_logging(True)

    from second_brain.llm.batch import BatchStatus
    from second_brain.pipeline.inbox import finalize_inbox_batch
    from second_brain.pipeline.newsletter import finalize_newsletter_batch

    config_dir = ctx.obj["config_dir"]
    settings, nl_config, taxonomy = _load_all_config(config_dir)

    batch_provider = _build_batch_provider(settings)
    batch_state = _build_batch_state(settings)
    pending_batches = batch_state.get_pending()

    if not pending_batches:
        click.echo("No pending batches.")
        return

    click.echo(f"Found {len(pending_batches)} pending batch(es).")

    vault = _build_vault(settings)
    sync_state = _build_sync_state(settings)
    gmail = _build_gmail(settings)

    finalized = 0
    still_pending = 0

    for pending in pending_batches:
        status = batch_provider.get_batch_status(pending.batch_id)
        click.echo(
            f"  {pending.batch_id}  pipeline={pending.pipeline}"
            f"  state={status.state}"
            f"  {status.succeeded}/{status.total} succeeded"
        )

        if not status.is_terminal:
            still_pending += 1
            continue

        if status.state == "complete":
            results = batch_provider.get_batch_results(pending.batch_id)

            if pending.pipeline == "newsletters":
                created, errors = finalize_newsletter_batch(
                    results=results,
                    pending=pending,
                    vault=vault,
                    settings=settings,
                    sync_state=sync_state,
                    dry_run=False,
                    gmail=gmail,
                )
                sync_state.update_global_last_run()
                click.echo(f"    → {created} notes created, {len(errors)} errors")
                for err in errors:
                    click.echo(f"    ! {err}", err=True)

            elif pending.pipeline == "inbox":
                created, skipped, errors = finalize_inbox_batch(
                    results=results,
                    pending=pending,
                    vault=vault,
                    settings=settings,
                    dry_run=False,
                )
                click.echo(f"    → {created} classified, {skipped} skipped, {len(errors)} errors")
                for err in errors:
                    click.echo(f"    ! {err}", err=True)

            finalized += 1

        else:
            # error or cancelled
            click.echo(f"    → Batch ended with state '{status.state}', removing from queue.", err=True)
            finalized += 1  # remove it regardless

        batch_state.remove_batch(pending.batch_id)

    click.echo(f"\nDone: {finalized} finalized, {still_pending} still in progress.")


# ---------------------------------------------------------------------------
# batch subcommand group
# ---------------------------------------------------------------------------

@cli.group()
def batch() -> None:
    """Inspect and manage pending LLM batch jobs."""
    pass


@batch.command("status")
@click.option("--refresh", is_flag=True, help="Fetch live status from the API for each batch")
@click.pass_context
def batch_status(ctx: click.Context, refresh: bool) -> None:
    """List all pending batches tracked in batch_state.yaml."""
    config_dir = ctx.obj["config_dir"]
    settings, _, _ = _load_all_config(config_dir)
    batch_state = _build_batch_state(settings)
    pending = batch_state.get_pending()

    if not pending:
        click.echo("No pending batches.")
        return

    batch_provider = _build_batch_provider(settings) if refresh else None

    for b in pending:
        line = (
            f"{b.batch_id}  pipeline={b.pipeline}"
            f"  submitted={b.submitted_at.strftime('%Y-%m-%d %H:%M UTC')}"
            f"  items={len(b.items)}"
            f"  expires={b.expires_at.strftime('%Y-%m-%d')}"
        )
        if refresh and batch_provider is not None:
            status = batch_provider.get_batch_status(b.batch_id)
            line += f"  state={status.state}  {status.succeeded}/{status.total}"
        click.echo(line)


@batch.command("cancel")
@click.argument("batch_id")
@click.pass_context
def batch_cancel(ctx: click.Context, batch_id: str) -> None:
    """Cancel a pending batch and remove it from the queue."""
    config_dir = ctx.obj["config_dir"]
    settings, _, _ = _load_all_config(config_dir)

    batch_provider = _build_batch_provider(settings)
    batch_state = _build_batch_state(settings)

    if batch_state.get_batch(batch_id) is None:
        click.echo(f"Batch {batch_id} not found in batch_state.yaml.", err=True)
        raise SystemExit(1)

    batch_provider.cancel_batch(batch_id)
    batch_state.remove_batch(batch_id)
    click.echo(f"Cancelled and removed {batch_id}.")


# ---------------------------------------------------------------------------
# vault subcommand group
# ---------------------------------------------------------------------------

@cli.group()
def vault() -> None:
    """Vault management commands."""
    pass


@vault.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing template files")
@click.pass_context
def vault_init(ctx: click.Context, force: bool) -> None:
    """Scaffold a new Obsidian vault with folders and note templates.

    Creates the six standard folders and copies the four Obsidian note
    templates (Newsletter, Web Clipping, Paper, Book) into 05 Templates/.
    The vault path is read from config/settings.yaml.

    This command is intentionally limited to taxonomy-agnostic scaffolding.
    Tag-specific database views (.base files) must be created manually inside
    Obsidian using the Bases plugin, once you have defined your taxonomy.
    """
    from importlib.resources import files as pkg_files

    config_dir = ctx.obj["config_dir"]
    try:
        settings, _, _ = _load_all_config(config_dir)
    except Exception as e:
        click.echo(f"Cannot read settings: {e}", err=True)
        raise SystemExit(1)

    vault_root = settings.vault.root
    click.echo(f"Vault root: {vault_root}")

    # 1. Create folders
    folders = ["00 Inbox", "01 Notes", "02 MOCs", "03 Bases", "04 Assets", "05 Templates"]
    click.echo("\nFolders:")
    for folder in folders:
        path = vault_root / folder
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        click.echo(f"  {'skipped' if existed else 'created '}  {folder}/")

    # 2. Copy Obsidian templates into 05 Templates/
    templates_dest = vault_root / "05 Templates"
    template_src = pkg_files("second_brain.scaffold") / "templates"
    template_names = [
        "Newsletter Template.md",
        "Web Clipping Template.md",
        "Paper Template.md",
        "Book Template.md",
    ]

    click.echo("\nObsidian templates:")
    for name in template_names:
        dest = templates_dest / name
        if dest.exists() and not force:
            click.echo(f"  skipped   05 Templates/{name}  (use --force to overwrite)")
            continue
        content = (template_src / name).read_text(encoding="utf-8")
        dest.write_text(content, encoding="utf-8")
        click.echo(f"  {'overwrote' if dest.exists() else 'created  '}  05 Templates/{name}")

    # 3. Post-init checklist
    click.echo("""
Next steps
----------
1. In Obsidian → Settings → Templates, set the template folder to "05 Templates".
2. Edit config/settings.yaml — confirm vault.root points to this vault.
3. Create config/taxonomy.yaml — required before the LLM pipeline can run.
4. Create config/newsletters.yaml — add your newsletter sources.
5. Create .base views manually inside Obsidian (Bases plugin) as needed.
   Structural starting points: Inbox (status=inbox), All Notes (type field),
   Newsletters (type=newsletter), Reading List (type=paper or book).
6. Run: second-brain config check
""")


# ---------------------------------------------------------------------------
# config subcommand group
# ---------------------------------------------------------------------------

@cli.group()
def config() -> None:
    """Configuration management commands."""
    pass


@config.command("check")
@click.pass_context
def config_check(ctx: click.Context) -> None:
    """Validate all configuration files."""
    config_dir = ctx.obj["config_dir"]
    try:
        settings, newsletters, taxonomy = _load_all_config(config_dir)
        click.echo(f"Settings:    OK ({settings.vault_backend} backend)")
        click.echo(f"Newsletters: OK ({len(newsletters.sources)} sources)")
        click.echo(f"Taxonomy:    OK ({len(taxonomy.descriptive)} descriptive + {len(taxonomy.functional)} functional tags)")
        click.echo("\nAll config files valid.")
    except Exception as e:
        click.echo(f"Config error: {e}", err=True)
        raise SystemExit(1)


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Print resolved configuration."""
    config_dir = ctx.obj["config_dir"]
    settings, newsletters, taxonomy = _load_all_config(config_dir)

    click.echo("=== Settings ===")
    click.echo(f"  Vault root:       {settings.vault.root}")
    click.echo(f"  Backend:          {settings.vault_backend}")
    click.echo(f"  LLM:              {settings.llm.provider} / {settings.llm.model}")
    click.echo(f"  Sync state:       {settings.vault.sync_state_file}")
    click.echo(f"  Batch state:      {settings.vault.batch_state_file}")
    click.echo(f"  Lookback days:    {settings.processing.default_lookback_days}")
    click.echo(f"  Batch poll:       {settings.llm.batch.poll_interval_seconds}s")
    click.echo(f"  Batch timeout:    {settings.llm.batch.timeout_hours}h")
    click.echo(f"\n=== Newsletters ({len(newsletters.sources)} sources) ===")
    for src in newsletters.sources:
        click.echo(f"  {src.name}: {src.email}")
    click.echo(f"\n=== Taxonomy ===")
    click.echo(f"  Descriptive tags: {len(taxonomy.descriptive)}")
    click.echo(f"  Functional tags:  {len(taxonomy.functional)}")
    click.echo(f"  Rules:            {len(taxonomy.classification_rules)}")


if __name__ == "__main__":
    cli()
