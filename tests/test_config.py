"""Tests for second_brain.config — loading, validation, and path expansion."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from second_brain.config import (
    GmailConfig,
    LLMConfig,
    NewsletterSource,
    NewslettersConfig,
    Settings,
    TaxonomyConfig,
    VaultConfig,
    load_newsletters,
    load_settings,
    load_taxonomy,
)


# ---------------------------------------------------------------------------
# Helpers — write minimal YAML fixtures into tmp_path
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)


def _minimal_settings() -> dict:
    """Return the minimal valid settings dict (only required fields)."""
    return {
        "vault": {"root": "/tmp/test-vault"},
    }


def _full_settings() -> dict:
    """Return a settings dict with every section populated."""
    return {
        "vault": {
            "root": "~/vaults/personal",
            "inbox_folder": "00 Inbox",
            "notes_folder": "01 Notes",
            "assets_folder": "04 Assets",
            "sync_state_file": "sync_state.yaml",
        },
        "vault_backend": "filesystem",
        "llm": {
            "provider": "claude",
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8192,
        },
        "gmail": {
            "credentials_file": "~/.config/sb/creds.json",
            "token_file": "~/.config/sb/token.json",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
        "processing": {
            "default_lookback_days": 14,
            "batch_size": 20,
            "dry_run": True,
        },
    }


def _sample_newsletters() -> dict:
    return {
        "sources": [
            {"email": "alice@example.com", "name": "Alice's Newsletter"},
            {"email": "bob@example.com", "name": "Bob's Digest"},
        ],
    }


def _sample_taxonomy() -> dict:
    return {
        "descriptive": {
            "ai/llm": "Large language models",
            "data/engineering": "Data engineering topics",
        },
        "functional": {
            "func/research": "Research-oriented content",
            "func/blog": "Blog-worthy content",
        },
        "classification_rules": [
            "Always assign at least one descriptive tag.",
            "Use functional tags sparingly.",
        ],
    }


# ===================================================================
# Settings loading
# ===================================================================


class TestLoadSettings:
    """Tests for load_settings()."""

    def test_load_minimal_settings(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "settings.yaml", _minimal_settings())
        settings = load_settings(tmp_path)

        assert isinstance(settings, Settings)
        assert settings.vault.root == Path("/tmp/test-vault")
        # Defaults should be applied
        assert settings.vault.inbox_folder == "00 Inbox"
        assert settings.vault.notes_folder == "01 Notes"
        assert settings.vault_backend == "filesystem"

    def test_load_full_settings(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "settings.yaml", _full_settings())
        settings = load_settings(tmp_path)

        assert settings.llm.max_tokens == 8192
        assert settings.processing.dry_run is True
        assert settings.processing.batch_size == 20
        assert settings.processing.default_lookback_days == 14

    def test_defaults_for_llm(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "settings.yaml", _minimal_settings())
        settings = load_settings(tmp_path)

        assert settings.llm.provider == "claude"
        assert settings.llm.model == "claude-sonnet-4-20250514"
        assert settings.llm.max_tokens == 4096

    def test_defaults_for_processing(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "settings.yaml", _minimal_settings())
        settings = load_settings(tmp_path)

        assert settings.processing.default_lookback_days == 7
        assert settings.processing.batch_size == 10
        assert settings.processing.dry_run is False


# ===================================================================
# Path expansion (tilde → home)
# ===================================================================


class TestPathExpansion:
    """Vault root and Gmail paths should expand ~ to the user's home."""

    def test_vault_root_tilde_expanded(self) -> None:
        vc = VaultConfig(root="~/my-vault")
        assert "~" not in str(vc.root)
        assert vc.root == Path.home() / "my-vault"

    def test_gmail_credentials_file_expanded(self) -> None:
        gc = GmailConfig(credentials_file="~/.config/creds.json")
        assert "~" not in str(gc.credentials_file)
        assert gc.credentials_file == Path.home() / ".config" / "creds.json"

    def test_gmail_token_file_expanded(self) -> None:
        gc = GmailConfig(token_file="~/.config/token.json")
        assert "~" not in str(gc.token_file)
        assert gc.token_file == Path.home() / ".config" / "token.json"

    def test_absolute_path_unchanged(self) -> None:
        vc = VaultConfig(root="/absolute/path/vault")
        assert vc.root == Path("/absolute/path/vault")


# ===================================================================
# Newsletters config
# ===================================================================


class TestLoadNewsletters:
    """Tests for load_newsletters()."""

    def test_load_newsletters(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "newsletters.yaml", _sample_newsletters())
        cfg = load_newsletters(tmp_path)

        assert isinstance(cfg, NewslettersConfig)
        assert len(cfg.sources) == 2
        assert cfg.sources[0].email == "alice@example.com"
        assert cfg.sources[1].name == "Bob's Digest"

    def test_newsletter_source_fields(self) -> None:
        ns = NewsletterSource(email="test@example.com", name="Test NL")
        assert ns.email == "test@example.com"
        assert ns.name == "Test NL"

    def test_empty_sources_list(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "newsletters.yaml", {"sources": []})
        cfg = load_newsletters(tmp_path)
        assert cfg.sources == []


# ===================================================================
# Taxonomy config
# ===================================================================


class TestLoadTaxonomy:
    """Tests for load_taxonomy() and TaxonomyConfig."""

    def test_load_taxonomy(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "taxonomy.yaml", _sample_taxonomy())
        cfg = load_taxonomy(tmp_path)

        assert isinstance(cfg, TaxonomyConfig)
        assert "ai/llm" in cfg.descriptive
        assert "func/research" in cfg.functional
        assert len(cfg.classification_rules) == 2

    def test_all_valid_tags_union(self) -> None:
        cfg = TaxonomyConfig(
            descriptive={"ai/llm": "LLMs", "data/eng": "Data eng"},
            functional={"func/blog": "Blog", "func/research": "Research"},
            classification_rules=[],
        )
        valid = cfg.all_valid_tags
        assert valid == {"ai/llm", "data/eng", "func/blog", "func/research"}

    def test_all_valid_tags_no_duplicates(self) -> None:
        """If the same key appears in both dicts the set should contain it once."""
        cfg = TaxonomyConfig(
            descriptive={"shared/tag": "desc1"},
            functional={"shared/tag": "desc2"},
            classification_rules=[],
        )
        assert cfg.all_valid_tags == {"shared/tag"}

    def test_all_valid_tags_empty(self) -> None:
        cfg = TaxonomyConfig(
            descriptive={},
            functional={},
            classification_rules=[],
        )
        assert cfg.all_valid_tags == set()


# ===================================================================
# Validation errors
# ===================================================================


class TestValidationErrors:
    """Pydantic should reject invalid / missing data."""

    def test_missing_vault_root(self) -> None:
        with pytest.raises(Exception):
            # vault.root is required
            VaultConfig()  # type: ignore[call-arg]

    def test_missing_vault_section_in_settings(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "settings.yaml", {"llm": {"provider": "openai"}})
        with pytest.raises(Exception):
            load_settings(tmp_path)

    def test_missing_newsletter_email(self) -> None:
        with pytest.raises(Exception):
            NewsletterSource(name="No Email")  # type: ignore[call-arg]

    def test_missing_newsletter_name(self) -> None:
        with pytest.raises(Exception):
            NewsletterSource(email="a@b.com")  # type: ignore[call-arg]

    def test_missing_taxonomy_fields(self) -> None:
        with pytest.raises(Exception):
            TaxonomyConfig(descriptive={"a": "b"})  # type: ignore[call-arg]

    def test_invalid_yaml_file(self, tmp_path: Path) -> None:
        (tmp_path / "settings.yaml").write_text(": bad: yaml: {{")
        with pytest.raises(Exception):
            load_settings(tmp_path)

    def test_nonexistent_config_dir(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "does_not_exist")
