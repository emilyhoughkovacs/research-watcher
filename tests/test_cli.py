"""CLI plumbing: `paths`, and environment-variable defaults with the guard
that keeps local writes off CI-owned state."""

import pytest

from research_watcher import cli


@pytest.fixture
def profile(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text(
        "output:\n"
        "  archive_dir: research/watch\n"
        "  guides_dir: research/repro-guides\n"
        "  state_file: research/.watch-state.json\n"
    )
    return p


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("RESEARCH_WATCH_SOURCES", "RESEARCH_WATCH_PROFILE",
                "RESEARCH_WATCH_BASE_DIR", "RESEARCH_WATCH_ENV"):
        monkeypatch.delenv(var, raising=False)


def test_paths_prints_profile_outputs(profile, capsys):
    cli.main(["--profile", str(profile), "--base-dir", ".", "paths"])
    assert capsys.readouterr().out.split() == [
        "research/watch", "research/repro-guides", "research/.watch-state.json",
    ]


def test_paths_defaults_when_profile_has_no_output_block(tmp_path, capsys):
    p = tmp_path / "profile.yaml"
    p.write_text("schedule: {cadence: daily}\n")
    cli.main(["--profile", str(p), "paths"])
    assert capsys.readouterr().out.split() == ["out/digest", "out/guides", "out/state.json"]


def test_env_defaults_apply_to_read_only_commands(profile, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RESEARCH_WATCH_PROFILE", str(profile))
    monkeypatch.setenv("RESEARCH_WATCH_BASE_DIR", str(tmp_path))
    cli.main(["paths"])
    assert capsys.readouterr().out.split()[0] == str(tmp_path / "research/watch")


@pytest.mark.parametrize("argv", [["baseline"], ["digest"], ["pick"]])
def test_env_base_dir_refused_for_state_writes(profile, tmp_path, monkeypatch, argv):
    monkeypatch.setenv("RESEARCH_WATCH_PROFILE", str(profile))
    monkeypatch.setenv("RESEARCH_WATCH_BASE_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="refusing to write state"):
        cli.main(argv)


def test_explicit_base_dir_wins_over_env(profile, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RESEARCH_WATCH_BASE_DIR", "/somewhere/else")
    cli.main(["--profile", str(profile), "--base-dir", str(tmp_path), "paths"])
    assert capsys.readouterr().out.split()[0] == str(tmp_path / "research/watch")
