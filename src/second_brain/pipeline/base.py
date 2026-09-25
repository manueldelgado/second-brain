"""Shared pipeline utilities — logging, error handling, reporting."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from second_brain.models import ContentAnalysis, NoteFrontmatter

logger = logging.getLogger(__name__)

# Jinja2 environment — loaded once, reused across pipelines
_template_env: Environment | None = None


def get_template_env(templates_dir: Path | None = None) -> Environment:
    """Get or create the Jinja2 template environment."""
    global _template_env
    if _template_env is None:
        td = templates_dir or Path(__file__).resolve().parents[3] / "templates"
        _template_env = Environment(
            loader=FileSystemLoader(str(td)),
            keep_trailing_newline=True,
        )
    return _template_env


def render_note(
    template_name: str,
    frontmatter: NoteFrontmatter,
    analysis: ContentAnalysis,
    content: str,
    extra: dict | None = None,
) -> str:
    """Render a complete note from a Jinja2 template."""
    env = get_template_env()
    template = env.get_template(template_name)
    context = {
        "frontmatter": frontmatter.to_yaml(),
        "summary": analysis.summary,
        "key_takeaways": analysis.key_takeaways,
        "content": content,
        **(extra or {}),
    }
    return template.render(**context)


# Emoji code points, plus the invisible joiners/modifiers that build compound
# emoji (ZWJ sequences, skin tones, flags, keycaps). Text symbols such as
# ©, ™, × and arrows are deliberately left alone.
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, transport, flags, ...
    "☀-➿"  # misc symbols + dingbats (☕ ✅ ✨ ❤)
    "⌚⌛⌨⏏⏩-⏳⏸-⏺"  # ⌚ ⏰ ⏩ ...
    "⬅-⬇⬛⬜⭐⭕"  # ⬅ ⬛ ⭐ ⭕
    "〰〽㊗㊙"
    "︎️‍⃣"  # variation selectors, ZWJ, keycap
    "\U000e0020-\U000e007f"  # tag sequences (subdivision flags)
    "]"
)


def sanitize_filename(title: str) -> str:
    """Convert a title to a safe filename for Obsidian."""
    # Emoji are visual noise in note names
    name = _EMOJI_RE.sub("", title)
    # Remove/replace characters that are problematic in filenames
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    # Obsidian cannot link to names containing #, ^, [ or ]: a wikilink to
    # "#064 Title" is read as a heading link. Brackets become parentheses.
    name = name.replace("[", "(").replace("]", ")")
    name = re.sub(r"#(\d+)([^\W\d_])", r"\1 \2", name)  # "#64Invest" -> "64 Invest"
    name = re.sub(r"[#^]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Truncate to reasonable length
    if len(name) > 200:
        name = name[:200].rsplit(" ", 1)[0]
    return (name or "Untitled") + ".md"


TEMPLATE_MAP = {
    "newsletter": "newsletter.md.j2",
    "clipping": "clipping.md.j2",
    "paper": "paper.md.j2",
    "book": "book.md.j2",
}


@dataclass
class PipelineReport:
    """Summary of a pipeline run."""

    pipeline_name: str
    items_processed: int = 0
    items_created: int = 0
    items_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def log_summary(self) -> None:
        logger.info(
            "[%s] Processed: %d | Created: %d | Skipped: %d | Errors: %d",
            self.pipeline_name,
            self.items_processed,
            self.items_created,
            self.items_skipped,
            len(self.errors),
        )
        for err in self.errors:
            logger.error("  Error: %s", err)

    def print_summary(self) -> None:
        print(f"\n{'='*50}")
        print(f"Pipeline: {self.pipeline_name}")
        print(f"  Processed: {self.items_processed}")
        print(f"  Created:   {self.items_created}")
        print(f"  Skipped:   {self.items_skipped}")
        print(f"  Errors:    {len(self.errors)}")
        if self.errors:
            for err in self.errors:
                print(f"    - {err}")
        print(f"{'='*50}")
