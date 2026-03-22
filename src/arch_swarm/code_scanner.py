"""Static analysis of project structure for architecture insights."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class ModuleInfo:
    """Metadata for a single Python module."""

    path: str
    name: str
    imports: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    lines: int = 0


@dataclass
class CouplingMetrics:
    """Afferent (incoming) and efferent (outgoing) coupling for a module."""

    module: str
    afferent: int = 0  # how many other modules depend on this one
    efferent: int = 0  # how many modules this one depends on

    @property
    def instability(self) -> float:
        """Robert C. Martin's instability metric: Ce / (Ca + Ce)."""
        total = self.afferent + self.efferent
        return self.efferent / total if total > 0 else 0.0


@dataclass
class ArchAnalysis:
    """Complete architecture analysis result."""

    root: str
    modules: list[ModuleInfo] = field(default_factory=list)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)
    coupling: list[CouplingMetrics] = field(default_factory=list)
    class_hierarchy: dict[str, list[str]] = field(default_factory=dict)
    complexity_scores: dict[str, int] = field(default_factory=dict)

    @property
    def total_modules(self) -> int:
        return len(self.modules)

    @property
    def total_lines(self) -> int:
        return sum(m.lines for m in self.modules)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _parse_module(filepath: Path, root: Path) -> Optional[ModuleInfo]:
    """Parse a single Python file into *ModuleInfo*."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    rel = str(filepath.relative_to(root)).replace(os.sep, "/")
    mod_name = rel.replace("/", ".").removesuffix(".py")

    info = ModuleInfo(path=rel, name=mod_name, lines=source.count("\n") + 1)

    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return info  # still return partial info

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                info.imports.append(node.module)
        elif isinstance(node, ast.ClassDef):
            info.classes.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            # Only top-level and class-level
            info.functions.append(node.name)

    return info


def _estimate_complexity(filepath: Path) -> int:
    """Rough cyclomatic-complexity approximation for a file.

    Counts branching keywords (if, elif, for, while, except, with, and, or).
    """
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return 0

    complexity = 1  # baseline
    for node in ast.walk(tree):
        if isinstance(node, ast.If | ast.IfExp):
            complexity += 1
        elif isinstance(node, ast.For | ast.AsyncFor):
            complexity += 1
        elif isinstance(node, ast.While):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, ast.With | ast.AsyncWith):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            # Each `and` / `or` adds a branch
            complexity += len(node.values) - 1
    return complexity


def _extract_class_hierarchy(filepath: Path) -> dict[str, list[str]]:
    """Map class -> list of base class names."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return {}

    hierarchy: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(ast.dump(base))
            if bases:
                hierarchy[node.name] = bases
    return hierarchy


def scan_project(
    project_path: str | Path,
    scope: str | None = None,
) -> ArchAnalysis:
    """Scan a Python project and return an *ArchAnalysis*.

    Parameters
    ----------
    project_path:
        Root directory of the project.
    scope:
        Optional sub-directory to restrict scanning (e.g. ``"src/"``).
    """
    root = Path(project_path).resolve()
    scan_root = root / scope if scope else root

    if not scan_root.is_dir():
        return ArchAnalysis(root=str(root))

    py_files = sorted(scan_root.rglob("*.py"))

    analysis = ArchAnalysis(root=str(root))

    for fp in py_files:
        mod = _parse_module(fp, root)
        if mod is None:
            continue
        analysis.modules.append(mod)
        analysis.dependency_graph[mod.name] = mod.imports
        analysis.complexity_scores[mod.name] = _estimate_complexity(fp)
        analysis.class_hierarchy.update(_extract_class_hierarchy(fp))

    # -- coupling metrics ----------------------------------------------------
    all_names = {m.name for m in analysis.modules}
    coupling_map: dict[str, CouplingMetrics] = {
        name: CouplingMetrics(module=name) for name in all_names
    }

    for mod in analysis.modules:
        seen: set[str] = set()
        for imp in mod.imports:
            # Match imports that refer to modules inside the project
            for target in all_names:
                if imp == target or imp.startswith(target + "."):
                    if target not in seen and target != mod.name:
                        seen.add(target)
                        coupling_map[mod.name].efferent += 1
                        coupling_map[target].afferent += 1

    analysis.coupling = list(coupling_map.values())
    return analysis


def format_analysis(analysis: ArchAnalysis) -> str:
    """Render an *ArchAnalysis* as a human-readable report."""
    lines: list[str] = []
    lines.append(f"Project: {analysis.root}")
    lines.append(f"Modules: {analysis.total_modules}  |  Lines: {analysis.total_lines}")
    lines.append("")

    if analysis.modules:
        lines.append("## Modules")
        for m in sorted(analysis.modules, key=lambda m: m.name):
            lines.append(f"  {m.name}  ({m.lines} lines, {len(m.classes)} classes, "
                         f"{len(m.functions)} functions)")
        lines.append("")

    if analysis.coupling:
        lines.append("## Coupling")
        for c in sorted(analysis.coupling, key=lambda c: c.module):
            lines.append(
                f"  {c.module}  Ca={c.afferent} Ce={c.efferent} "
                f"I={c.instability:.2f}"
            )
        lines.append("")

    if analysis.complexity_scores:
        lines.append("## Complexity (approx. cyclomatic)")
        for name, score in sorted(
            analysis.complexity_scores.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {name}: {score}")
        lines.append("")

    if analysis.class_hierarchy:
        lines.append("## Class Hierarchy")
        for cls, bases in sorted(analysis.class_hierarchy.items()):
            lines.append(f"  {cls} -> {', '.join(bases)}")
        lines.append("")

    return "\n".join(lines)
