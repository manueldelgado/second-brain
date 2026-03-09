"""Configuration loader and validation using Pydantic."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class VaultConfig(BaseModel):
    root: Path
    inbox_folder: str = "00 Inbox"
    notes_folder: str = "01 Notes"
    assets_folder: str = "04 Assets"
    sync_state_file: str = "sync_state.yaml"
    batch_state_file: str = "batch_state.yaml"

    @field_validator("root", mode="before")
    @classmethod
    def expand_root(cls, v: str) -> Path:
        return Path(v).expanduser()


class BatchConfig(BaseModel):
    poll_interval_seconds: int = 30
    timeout_hours: int = 24


class LLMConfig(BaseModel):
    provider: str = "claude"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    batch: BatchConfig = BatchConfig()


class GmailConfig(BaseModel):
    credentials_file: Path = Path("~/.config/second-brain/gmail_credentials.json")
    token_file: Path = Path("~/.config/second-brain/gmail_token.json")
    scopes: list[str] = ["https://www.googleapis.com/auth/gmail.modify"]

    @field_validator("credentials_file", "token_file", mode="before")
    @classmethod
    def expand_paths(cls, v: str) -> Path:
        return Path(v).expanduser()


class ProcessingConfig(BaseModel):
    default_lookback_days: int = 7
    batch_size: int = 10
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Newsletter source
# ---------------------------------------------------------------------------

class NewsletterSource(BaseModel):
    email: str
    name: str
    sender_name: str | None = None


class NewslettersConfig(BaseModel):
    sources: list[NewsletterSource]


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

class TaxonomyConfig(BaseModel):
    descriptive: dict[str, str]
    functional: dict[str, str]
    classification_rules: list[str]

    @property
    def all_valid_tags(self) -> set[str]:
        return set(self.descriptive) | set(self.functional)


# ---------------------------------------------------------------------------
# Top-level settings
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    vault: VaultConfig
    vault_backend: str = "filesystem"
    llm: LLMConfig = LLMConfig()
    gmail: GmailConfig = GmailConfig()
    processing: ProcessingConfig = ProcessingConfig()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_settings(config_dir: Path | None = None) -> Settings:
    """Load and validate settings.yaml."""
    config_dir = config_dir or Path("config")
    data = _load_yaml(config_dir / "settings.yaml")
    return Settings(**data)


def load_newsletters(config_dir: Path | None = None) -> NewslettersConfig:
    """Load and validate newsletters.yaml."""
    config_dir = config_dir or Path("config")
    data = _load_yaml(config_dir / "newsletters.yaml")
    return NewslettersConfig(**data)


def load_taxonomy(config_dir: Path | None = None) -> TaxonomyConfig:
    """Load and validate taxonomy.yaml."""
    config_dir = config_dir or Path("config")
    data = _load_yaml(config_dir / "taxonomy.yaml")
    return TaxonomyConfig(**data)
