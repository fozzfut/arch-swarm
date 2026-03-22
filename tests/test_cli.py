"""Tests for arch_swarm.cli."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from arch_swarm.cli import main


class TestAnalyzeCommand:
    def test_analyze_runs(self, tmp_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", str(tmp_project), "--scope", "src/myapp"])
        assert result.exit_code == 0
        assert "Modules" in result.output

    def test_analyze_no_scope(self, tmp_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", str(tmp_project)])
        assert result.exit_code == 0


class TestDebateCommand:
    def test_debate_runs(self, tmp_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["debate", str(tmp_project), "--topic", "Split core.py?"],
        )
        assert result.exit_code == 0
        assert "Debate:" in result.output or "Proposal" in result.output

    def test_debate_saves_session(self, tmp_project: Path, tmp_path: Path, monkeypatch: object) -> None:
        import arch_swarm.cli as cli_mod

        session_dir = tmp_path / "sessions"
        monkeypatch.setattr(cli_mod, "_SESSION_DIR", session_dir)  # type: ignore[attr-defined]

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["debate", str(tmp_project), "--topic", "Test topic"],
        )
        assert result.exit_code == 0
        md_files = list(session_dir.glob("*.md"))
        assert len(md_files) == 1
        json_files = list(session_dir.glob("*.json"))
        assert len(json_files) == 1


class TestReportCommand:
    def test_report_missing_session(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["report", "nonexistent"])
        assert result.exit_code == 1

    def test_report_existing_session(self, tmp_path: Path, monkeypatch: object) -> None:
        import arch_swarm.cli as cli_mod

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setattr(cli_mod, "_SESSION_DIR", session_dir)  # type: ignore[attr-defined]

        (session_dir / "abc123.md").write_text("# Debate transcript\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["report", "abc123"])
        assert result.exit_code == 0
        assert "Debate transcript" in result.output
