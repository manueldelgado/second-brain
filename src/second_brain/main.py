"""CLI entry point for the Second Brain automation."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

_RUN_LOG = "~/.local/log/second-brain/run.log"


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


def _load_settings(config_dir: Path):
    """Load settings.yaml alone — it locates the vault, so its errors can't become an alert note."""
    from second_brain.config import load_settings

    try:
        return load_settings(config_dir)
    except Exception as exc:
        raise click.ClickException(
            f"{config_dir / 'settings.yaml'} is invalid (no alert note: the vault location "
            f"comes from this file): {exc}"
        ) from exc


def _load_pipeline_config(config_dir: Path):
    """Load newsletters.yaml and taxonomy.yaml, turning any failure into a ConfigError.

    Both pipelines load both files, so a run that gets past this proves the whole
    config is valid and may clear the "Config" alert.
    """
    from second_brain.config import load_newsletters, load_taxonomy
    from second_brain.errors import ConfigError

    loaded = []
    for filename, loader in (("newsletters.yaml", load_newsletters), ("taxonomy.yaml", load_taxonomy)):
        try:
            loaded.append(loader(config_dir))
        except Exception as exc:
            raise ConfigError(
                f"`{config_dir / filename}` could not be loaded: {exc}",
                hint=f"Fix `{filename}` (the error above gives the line and column), then "
                "check it with `second-brain config check`. If the file lives in the private "
                "config repo, `git diff` in `config/` shows the latest edit.",
            ) from exc
    return tuple(loaded)


def _build_vault(settings):
    from second_brain.vault.filesystem import FilesystemBackend
    from second_brain.vault.obsidian_cli import ObsidianCLIBackend

    if settings.vault_backend == "obsidian_cli":
        return ObsidianCLIBackend(settings.vault.root, settings.vault.vault_name)
    return FilesystemBackend(settings.vault.root)


def _build_llm(settings):
    if settings.llm.provider == "claude_cli":
        from second_brain.llm.claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(model=settings.llm.model, effort=settings.llm.effort)

    from second_brain.llm.claude import ClaudeProvider

    return ClaudeProvider(
        model=settings.llm.model,
        max_tokens=settings.llm.max_tokens,
        thinking=settings.llm.thinking,
        effort=settings.llm.effort,
    )


@contextmanager
def _monitored_run(settings, command: str, dry_run: bool, components: tuple[str, ...]):
    """Make an unattended pipeline run visible in the vault (see ``alerts.py``).

    Yields a dict; the command stores its PipelineReport under ``"report"``.
    A BlockingError becomes an alert note for its component and a failed
    command. A run that completes updates the status note and clears the alerts
    of the *components* it exercised: "Config" (both pipelines load every config
    file), "Gmail" (the newsletters run fetches from it), "Claude" (building the
    CLI provider checks its login).

    Any other exception (a bug, an unreadable vault) becomes an alert for the
    command itself ("Newsletters", "Inbox"), cleared by its next completed run —
    otherwise a crash would only show in the log while the status note, kept
    fresh by the other pipeline, looks healthy.
    """
    from second_brain.alerts import clear_alert, raise_alert, record_run
    from second_brain.errors import BlockingError

    folder = Path(settings.vault.root) / settings.vault.notes_folder
    crash_component = command.capitalize()
    run: dict = {}
    try:
        yield run
    except BlockingError as exc:
        if not dry_run:
            raise_alert(folder, exc.component, str(exc), exc.hint, command)
        raise click.ClickException(f"{exc}\n{exc.hint}") from exc
    except (click.ClickException, click.Abort, click.exceptions.Exit):
        raise  # usage errors and the like, meant for whoever typed the command
    except Exception as exc:
        logger.exception("%s run failed with an unexpected error", command)
        problem = f"The {command} pipeline stopped with an unexpected error:\n\n`{type(exc).__name__}: {exc}`"
        hint = (
            f"The full traceback is in `{_RUN_LOG}`. A transient problem (network, iCloud) "
            "goes away by itself: the next run that completes deletes this note. If the "
            "failed-run count keeps growing, the error repeats on every run and needs a fix."
        )
        if not dry_run:
            raise_alert(folder, crash_component, problem, hint, command)
        raise click.ClickException(f"{command} failed: {type(exc).__name__}: {exc}") from exc
    if dry_run:
        return
    clear_alert(folder, crash_component)
    for component in components:
        if component != "Claude" or settings.llm.provider == "claude_cli":
            clear_alert(folder, component)
    report = run.get("report")
    if report is not None:
        record_run(
            folder,
            command,
            f"{report.items_processed} processed, {report.items_created} created",
            len(report.errors),
        )


def _check_batch_supported(settings, batch: bool) -> None:
    if batch and settings.llm.provider == "claude_cli":
        raise click.UsageError(
            "--batch uses the Anthropic Batches API; it is not available with "
            "llm.provider 'claude_cli' (runs are synchronous)."
        )


def _build_batch_provider(settings):
    from second_brain.llm.claude_batch import ClaudeBatchProvider

    return ClaudeBatchProvider(
        model=settings.llm.model,
        max_tokens=settings.llm.max_tokens,
        thinking=settings.llm.thinking,
        effort=settings.llm.effort,
    )


def _build_gmail(settings, interactive: bool | None = None):
    from second_brain.gmail.client import GmailClient

    return GmailClient(
        credentials_file=settings.gmail.credentials_file,
        token_file=settings.gmail.token_file,
        scopes=settings.gmail.scopes,
        interactive=interactive,
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
    from second_brain.vault.icloud import enable_dataless_materialization

    _setup_logging(verbose)
    # Before any vault read: under launchd, iCloud placeholders would raise EDEADLK.
    enable_dataless_materialization()
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
    settings = _load_settings(config_dir)
    dry_run = dry_run or settings.processing.dry_run
    with _monitored_run(settings, "newsletters", dry_run, ("Config", "Gmail", "Claude")) as run:
        nl_config, taxonomy = _load_pipeline_config(config_dir)

        vault = _build_vault(settings)
        gmail = _build_gmail(settings)
        _check_batch_supported(settings, batch)
        sync_state = _build_sync_state(settings)

        batch_provider = _build_batch_provider(settings) if batch else None
        batch_state = _build_batch_state(settings) if batch else None

        llm = _build_llm(settings)
        run["report"] = run_newsletter_pipeline(
            settings=settings,
            newsletters=nl_config,
            taxonomy=taxonomy,
            vault=vault,
            gmail=gmail,
            llm=llm,
            sync_state=sync_state,
            dry_run=dry_run,
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
    settings = _load_settings(config_dir)
    dry_run = dry_run or settings.processing.dry_run
    with _monitored_run(settings, "inbox", dry_run, ("Config", "Claude")) as run:
        _, taxonomy = _load_pipeline_config(config_dir)

        vault = _build_vault(settings)
        _check_batch_supported(settings, batch)

        batch_provider = _build_batch_provider(settings) if batch else None
        batch_state = _build_batch_state(settings) if batch else None

        llm = _build_llm(settings)
        run["report"] = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
            dry_run=dry_run,
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
    from second_brain.errors import ConfigError
    from second_brain.llm.base import LLMUnavailableError

    try:
        ctx.invoke(newsletters, dry_run=dry_run, verbose=verbose, batch=batch, no_wait=no_wait)
    except click.ClickException as exc:
        # A Gmail problem shouldn't stop the inbox; a Claude or config one would block it too
        # (running it would only count the same failure twice in the alert note).
        if isinstance(exc.__cause__, (LLMUnavailableError, ConfigError)):
            raise
        logger.error("Newsletters failed, continuing with inbox: %s", exc.message)
        ctx.invoke(inbox, dry_run=dry_run, verbose=verbose, batch=batch, no_wait=no_wait)
        raise
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
                    taxonomy=taxonomy,
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
# gmail
# ---------------------------------------------------------------------------

@cli.group()
def gmail() -> None:
    """Gmail authorization."""


@gmail.command("login")
@click.pass_context
def gmail_login(ctx: click.Context) -> None:
    """Authorize Gmail access (opens a browser if the saved token is missing or revoked)."""
    from second_brain.alerts import clear_alert

    settings, _, _ = _load_all_config(ctx.obj["config_dir"])
    client = _build_gmail(settings, interactive=True)
    profile = client.service.users().getProfile(userId="me").execute()
    click.echo(f"Gmail authorized for {profile['emailAddress']} (token: {settings.gmail.token_file})")
    clear_alert(Path(settings.vault.root) / settings.vault.notes_folder, "Gmail")


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
