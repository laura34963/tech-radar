from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_REQUIRED = {
    "rss": ["url"], "cloud": ["url"], "github": ["repo"],
    "security": ["feed"], "social": ["source"],
    "registry": ["registry", "packages"],
}


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
    # keywords is boosted to "high" (see fetch.score_importance). Lets a
    # category like "ai" surface content that never touches [stack] terms.
    category_keywords: dict = field(default_factory=dict)


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
    return Config(
        general=raw.get("general", {}),
        stack=raw.get("stack", {}),
        categories=categories,
        sources=sources,
        llm=raw.get("llm", {}),
        category_keywords=raw.get("category_keywords", {}),
    )
