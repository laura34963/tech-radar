import textwrap
from pathlib import Path
import pytest
from radar.config import load_config, ConfigError


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "radar.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_loads_valid_config(tmp_path):
    cfg = load_config(_write(tmp_path, """
        categories = ["backend", "security"]
        [general]
        title = "T"
        min_keep_importance = "medium"
        min_display_importance = "high"
        [stack]
        packages = ["rails"]
        [[sources]]
        type = "rss"
        category = "backend"
        url = "https://x/feed"
        [llm]
        enabled = false
    """))
    assert cfg.general["title"] == "T"
    assert cfg.categories == ["backend", "security"]
    assert cfg.sources[0]["type"] == "rss"
    assert cfg.llm["enabled"] is False


def test_unknown_source_type_raises(tmp_path):
    with pytest.raises(ConfigError, match="unknown source type 'blog'"):
        load_config(_write(tmp_path, """
            categories = ["backend"]
            [[sources]]
            type = "blog"
            category = "backend"
        """))


def test_missing_required_field_raises(tmp_path):
    with pytest.raises(ConfigError, match="rss.*requires 'url'"):
        load_config(_write(tmp_path, """
            categories = ["backend"]
            [[sources]]
            type = "rss"
            category = "backend"
        """))


def test_registry_source_loads(tmp_path):
    cfg = load_config(_write(tmp_path, """
        categories = ["frontend"]
        [[sources]]
        type = "registry"
        category = "frontend"
        registry = "npm"
        packages = ["react", "axios"]
    """))
    assert cfg.sources[0]["registry"] == "npm"
    assert cfg.sources[0]["packages"] == ["react", "axios"]


def test_registry_missing_packages_raises(tmp_path):
    with pytest.raises(ConfigError, match="registry.*requires 'packages'"):
        load_config(_write(tmp_path, """
            categories = ["frontend"]
            [[sources]]
            type = "registry"
            category = "frontend"
            registry = "npm"
        """))
