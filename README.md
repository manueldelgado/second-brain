# Second Brain Automation

Automated newsletter ingestion and inbox classification for your Obsidian Second Brain vault.

## Features

- **Newsletter Ingestion** — Fetch emails from any number of newsletter sources, summarize with Claude, auto-classify with tags, create notes
- **Inbox Classification** — Scan inbox, classify untagged items, move to notes
- **Batch Mode** — Submit all items to Anthropic's batch API in one call (50% cost reduction); fire-and-forget with `--no-wait` and finalize later with `resume-batch`
- **Prompt Caching** — System prompt cached across calls; saves ~90% of system-prompt token cost per run
- **Gmail Labelling** — Processed emails are automatically labelled "Newsletters" in Gmail (label created if absent)
- **Smart Deduplication** — Two-layer Gmail filtering (coarse date query + precise `internalDate` client-side check) prevents re-processing emails from the same calendar day
- **Content Extraction** — Clean HTML emails to extract main content (removes boilerplate, ads, navigation)
- **Smart Error Recovery** — Malformed LLM responses are parsed gracefully; one failure doesn't stop the batch
- **Dry-Run Mode** — Preview pipeline output without writing to vault or touching Gmail

## Quick Start

### 1. Install Dependencies

```bash
pip install -e .
```

### 2. Set Up API Keys

**Anthropic (Claude):**
- Go to [console.anthropic.com](https://console.anthropic.com/settings/api-keys)
- Create an API key and add credits
- Create `.env`:

```bash
cp .env.example .env
# Edit .env and paste your API key
```

**Gmail:**
- First run opens a browser for OAuth consent (`gmail.modify` scope required for labelling)
- Credentials: `~/.config/second-brain/gmail_credentials.json`
- Token auto-refreshed: `~/.config/second-brain/gmail_token.json`

### 3. Configure and Scaffold the Vault

The `config/` directory is a private git submodule and will be empty after cloning. Copy the provided examples as a starting point:

```bash
cp -r config.example/ config/
# Edit config/settings.yaml  — set your vault path
# Edit config/newsletters.yaml — add your newsletter sources
# Edit config/taxonomy.yaml  — define your tag vocabulary
```

Then validate and scaffold:

```bash
second-brain config check       # verify settings are valid
second-brain vault init         # create folders + copy note templates
```

`vault init` creates the six standard folders (`00 Inbox/` through `05 Templates/`) and installs the four note templates (Newsletter, Web Clipping, Paper, Book) into `05 Templates/`. It is safe to re-run — existing templates are skipped unless you pass `--force`.

> **What `vault init` does not create:** tag-specific database views (`.base` files). These depend on your personal taxonomy and must be built inside Obsidian using the Bases plugin after you have defined `config/taxonomy.yaml`. Good structural starting points: filter by `status: inbox` (Inbox), `type` field (All Notes), `type: newsletter` (Newsletters), `type: paper` or `type: book` (Reading List).

### 4. Test with Dry-Run

```bash
second-brain newsletters --dry-run -v
second-brain inbox --dry-run -v
second-brain run --dry-run -v       # Both pipelines
```

### 5. Run for Real

```bash
# Synchronous (one item at a time)
second-brain newsletters
second-brain inbox
second-brain run

# Batch mode (all items submitted at once — 50% cheaper)
second-brain newsletters --batch
second-brain run --batch

# Batch + fire-and-forget (submit now, finalize later)
second-brain run --batch --no-wait
second-brain resume-batch           # Run once results are ready
```

## Configuration

- `config/settings.yaml` — Vault path, LLM model, Gmail scopes, batch settings, processing defaults
- `config/newsletters.yaml` — Newsletter sources (email → name). New entries are picked up automatically on the next run.
- `config/taxonomy.yaml` — 45 tags (33 descriptive + 12 functional) + classification rules

## Vault Setup

```
Personal (Obsidian vault root)
├── 00 Inbox/           ← unclassified items
├── 01 Notes/           ← all classified notes
├── 02 MOCs/            ← curated topic maps
├── 03 Bases/           ← database views
├── 04 Assets/          ← PDFs, images
└── 05 Templates/       ← Obsidian templates
```

Each newsletter note has:
- **Frontmatter** — title, source, author, newsletter, published, `gmail_url` (link to original email), created, type, status, tags, description
- **Summary** — AI-generated 2-4 sentence summary (always in English)
- **Key Takeaways** — bullet points
- **Content** — full original email text
- **My Notes / Related** — empty on creation, for personal use

## How It Works

### Newsletter Pipeline

1. Read `sync_state.yaml` for per-source last-processed timestamps.
2. For each source, compute a two-layer cutoff:
   - **Coarse:** Gmail `after:YYYY/MM/DD` query (day-granular)
   - **Precise:** client-side `internalDate` filter (millisecond precision) — skips emails already processed from the same calendar day
   - New sources with no sync entry use `now - default_lookback_days` (default: 7 days)
3. For each new email: extract text → send to Claude → create note in `01 Notes/` → apply "Newsletters" Gmail label → update `sync_state.yaml`

**Batch mode** collects all items first, submits one API call, then either polls inline or saves to `batch_state.yaml` and exits. `resume-batch` finalizes any pending batches.

### Inbox Pipeline

1. Scans `00 Inbox/` for items with `status: inbox` or missing frontmatter.
2. Sends each to Claude; if tags are returned, moves to `01 Notes/`; if not, leaves for manual review.
3. PDFs: copied to `04 Assets/`, wrapper note created, original deleted.

## Batch Job Management

```bash
second-brain resume-batch              # Poll all pending, finalize completed
second-brain batch status              # List pending batches (from batch_state.yaml)
second-brain batch status --refresh    # Same, with live API status
second-brain batch cancel <batch_id>   # Cancel and remove a batch
```

## Sync State

`sync_state.yaml` tracks the last processed email per source. Adding a new newsletter source to `newsletters.yaml` requires no manual changes — its entry is created automatically on first run.

To force a re-process window, edit the timestamp for a source:
```yaml
last_sync:
  "Benedict Evans": "2026-03-01T00:00:00Z"  # reprocess from this date forward
```

## Cost

Using **Haiku** (default): ~$0.01–0.03 per newsletter item in sync mode. With `--batch`: ~50% reduction.

Prompt caching is enabled by default and reduces system-prompt token cost by ~90% from the second call onward within a run.

## Troubleshooting

**`insufficientPermissions` on Gmail**
The token was issued with `gmail.readonly`. Delete it and re-authenticate:
```bash
rm ~/.config/second-brain/gmail_token.json
second-brain newsletters --dry-run   # triggers OAuth browser flow
```

**"Your credit balance is too low"**
Add credits at [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).

**"Gmail API error" / missing credentials**
Ensure `~/.config/second-brain/gmail_credentials.json` exists (download OAuth client credentials from Google Cloud Console).

**New newsletter source not finding emails**
Check that the source has no `last_sync` entry in `sync_state.yaml` — if one exists with a recent timestamp it will be used as the cutoff. Remove the entry to fall back to `default_lookback_days`.

## Development

```bash
pytest                                          # All tests
pytest --cov=src/second_brain tests/            # With coverage
pytest tests/test_pipeline_newsletter.py -v     # Specific file
```

109 tests, all using mocks for external APIs and temp directories for file operations.

## Architecture

The code is structured around a sync pipeline (`pipeline/newsletter.py`, `pipeline/inbox.py`), a provider-agnostic LLM layer (`llm/`), and a vault abstraction (`vault/`). Start with `src/second_brain/main.py` for the CLI entry point.
