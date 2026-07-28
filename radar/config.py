from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_REQUIRED = {
    "rss": ["url"], "cloud": ["url"], "github": ["repo"],
    "security": ["feed"], "social": ["source"],
    "registry": ["registry", "packages"],
}

_BOARDS = {"tech", "news"}

# `[exclude]` keys are category names plus this one reserved word meaning
# "every category", so a category may not be called it.
_RESERVED_CATEGORY = "global"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    general: dict
    stack: dict
    categories: list[str]
    sources: list[dict]
    llm: dict
    # per-category keyword lists; an item matching one of its own category's
    # keywords is boosted to "high" (see match.score_importance). Lets a
    # category like "ai" surface content that never touches [stack] terms.
    category_keywords: dict = field(default_factory=dict)
    # exclusion keywords: {"global": [...], "<category>": [...]}. An item matching
    # any applicable term is dropped at fetch, never written to the snapshot.
    exclude: dict = field(default_factory=dict)


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e

    categories = raw.get("categories") or ["backend", "frontend", "devops", "cloud", "security"]
    sources = raw.get("sources", [])
    for i, s in enumerate(sources):
        label = f"sources[{i}]"
        stype = s.get("type")
        if stype not in _REQUIRED:
            raise ConfigError(f"{label}: unknown source type {stype!r}")
        if not s.get("category"):
            raise ConfigError(f"{label}: requires 'category'")
        for field_name in _REQUIRED[stype]:
            if not s.get(field_name):
                raise ConfigError(f"{label}: type '{stype}' requires {field_name!r}")
        board = s.get("board")
        if board is not None and board not in _BOARDS:
            raise ConfigError(
                f"{label}: board must be one of {sorted(_BOARDS)}, got {board!r}"
            )
    if _RESERVED_CATEGORY in categories:
        raise ConfigError(
            f"categories must not contain {_RESERVED_CATEGORY!r}: it is reserved as "
            f"the [exclude] key meaning 'every category'")
    exclude = raw.get("exclude", {})
    if not isinstance(exclude, dict):
        raise ConfigError("[exclude] must be a table")
    allowed = set(categories) | {_RESERVED_CATEGORY}
    for key, terms in exclude.items():
        if key not in allowed:
            raise ConfigError(
                f"exclude.{key}: unknown key; expected {_RESERVED_CATEGORY!r} "
                f"or one of {sorted(categories)}")
        if not isinstance(terms, list) or not all(
                isinstance(t, str) and t.strip() for t in terms):
            raise ConfigError(f"exclude.{key}: must be a list of non-empty strings")
    return Config(
        general=raw.get("general", {}),
        stack=raw.get("stack", {}),
        categories=categories,
        sources=sources,
        llm=raw.get("llm", {}),
        category_keywords=raw.get("category_keywords", {}),
        exclude=exclude,
    )
