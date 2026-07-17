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
