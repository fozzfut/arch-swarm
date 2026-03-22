"""Tests for arch_swarm.code_scanner."""

from __future__ import annotations

from pathlib import Path

from arch_swarm.code_scanner import format_analysis, scan_project


class TestScanProject:
    def test_discovers_modules(self, tmp_project: Path) -> None:
        analysis = scan_project(tmp_project, scope="src/myapp")
        names = {m.name for m in analysis.modules}
        assert "myapp" in names
        assert "myapp.core" in names
        assert "myapp.utils" in names

    def test_module_line_counts(self, tmp_project: Path) -> None:
        analysis = scan_project(tmp_project, scope="src/myapp")
        for mod in analysis.modules:
            assert mod.lines > 0

    def test_imports_captured(self, tmp_project: Path) -> None:
        analysis = scan_project(tmp_project, scope="src/myapp")
        core = next(m for m in analysis.modules if m.name == "myapp.core")
        assert "os" in core.imports
        assert "myapp" in core.imports or any("myapp" in i for i in core.imports)

    def test_class_hierarchy(self, tmp_project: Path) -> None:
        analysis = scan_project(tmp_project, scope="src/myapp")
        assert "Child" in analysis.class_hierarchy
        assert "Base" in analysis.class_hierarchy["Child"]

    def test_complexity_scores(self, tmp_project: Path) -> None:
        analysis = scan_project(tmp_project, scope="src/myapp")
        # core.py has an if-statement so complexity > 1
        assert analysis.complexity_scores["myapp.core"] > 1

    def test_empty_directory(self, tmp_path: Path) -> None:
        analysis = scan_project(tmp_path, scope="nonexistent")
        assert analysis.total_modules == 0

    def test_format_analysis_output(self, tmp_project: Path) -> None:
        analysis = scan_project(tmp_project, scope="src/myapp")
        text = format_analysis(analysis)
        assert "Modules" in text
        assert "Coupling" in text
        assert "Complexity" in text
