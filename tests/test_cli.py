import httpx
from radar import cli as cli_module
from radar.cli import main


def test_config_error_exits_2(tmp_path, capsys):
    bad = tmp_path / "radar.toml"
    bad.write_text('[[sources]]\ntype="blog"\ncategory="x"\n')
    code = main(["fetch", "--config", str(bad), "--output", str(tmp_path / "o")])
    assert code == 2
    assert "unknown source type" in capsys.readouterr().err


def test_run_end_to_end_rule_based(tmp_path, monkeypatch):
    # a config with zero sources + llm disabled => empty but valid digest + index
    cfg = tmp_path / "radar.toml"
    cfg.write_text('categories=["backend"]\n[llm]\nenabled=false\n')
    out = tmp_path / "o"
    code = main(["run", "--config", str(cfg), "--output", str(out)])
    assert code == 0
    assert (out / "index.html").exists()


def test_run_returns_1_when_all_sources_fail(tmp_path, monkeypatch):
    real_client_cls = httpx.Client

    def fake_client(*args, **kwargs):
        return real_client_cls(transport=httpx.MockTransport(
            lambda req: httpx.Response(500)))

    monkeypatch.setattr(cli_module.httpx, "Client", fake_client)

    cfg = tmp_path / "radar.toml"
    cfg.write_text(
        'categories=["backend"]\n'
        '[[sources]]\n'
        'type="rss"\n'
        'category="backend"\n'
        'url="https://example.com/feed.xml"\n'
        '[llm]\nenabled=false\n'
    )
    out = tmp_path / "o"
    code = main(["run", "--config", str(cfg), "--output", str(out)])
    assert code == 1
