"""MCP Server for ArchSwarm -- architecture analysis and debate tools."""

import json
import logging
import secrets
from typing import Optional
from pathlib import Path

_log = logging.getLogger("arch_swarm.server")


# ---------------------------------------------------------------------------
# swarm-kb integration -- post findings, debates, decisions
# ---------------------------------------------------------------------------

def _post_findings_to_kb(analysis, session_id: str) -> int:
    """Convert analysis metrics to findings and post to swarm-kb.

    Returns the number of findings posted, or 0 on failure.
    """
    try:
        from swarm_kb.finding_writer import FindingWriter
        from swarm_kb.config import SuiteConfig
    except ImportError:
        _log.warning("swarm-kb not installed; skipping finding post")
        return 0

    try:
        config = SuiteConfig.load()
        writer = FindingWriter(tool="arch", session_id=session_id, config=config)
    except Exception as exc:
        _log.warning("Failed to initialise FindingWriter: %s", exc)
        return 0

    findings: list[dict] = []
    coupling_map = {c.module: c for c in analysis.coupling}

    # 1. High efferent coupling (>= 8 outgoing dependencies)
    for c in analysis.coupling:
        if c.efferent >= 8:
            sev = "high" if c.efferent >= 12 else "medium"
            mod = next((m for m in analysis.modules if m.name == c.module), None)
            file_path = mod.path if mod else c.module.replace(".", "/") + ".py"
            findings.append({
                "id": "af-" + secrets.token_hex(3),
                "file": file_path,
                "line_start": 1,
                "line_end": 1,
                "severity": sev,
                "category": "architecture",
                "title": f"High outgoing coupling: {c.module} ({c.efferent} dependencies)",
                "actual": f"Module {c.module} imports {c.efferent} other project modules",
                "expected": "Modules should have low efferent coupling (< 8) for maintainability",
                "suggestion_action": "refactor",
                "suggestion_detail": (
                    f"Break {c.module}'s {c.efferent} outgoing dependencies by "
                    "extracting shared concerns into focused sub-modules"
                ),
                "confidence": 0.8,
                "tags": ["coupling", "modularity", "refactoring"],
            })

    # 2. Circular dependencies
    circular = _find_circular_deps(analysis.dependency_graph)
    for mod_a, mod_b in circular:
        mod = next((m for m in analysis.modules if m.name == mod_a), None)
        file_path = mod.path if mod else mod_a.replace(".", "/") + ".py"
        findings.append({
            "id": "af-" + secrets.token_hex(3),
            "file": file_path,
            "line_start": 1,
            "line_end": 1,
            "severity": "high",
            "category": "architecture",
            "title": f"Circular dependency: {mod_a} <-> {mod_b}",
            "actual": f"{mod_a} imports {mod_b} and {mod_b} imports {mod_a}",
            "expected": "Dependencies should be acyclic (DAG) to prevent build/test fragility",
            "suggestion_action": "fix",
            "suggestion_detail": (
                f"Break cycle by extracting shared types/interfaces into a third module, "
                f"or use dependency inversion (abstract interface in {mod_a}, "
                f"implementation in {mod_b})"
            ),
            "confidence": 0.9,
            "tags": ["circular-dependency", "modularity", "architecture"],
        })

    # 3. High complexity modules (bottleneck risk: complexity >= 30 with afferent >= 2)
    for mod_name, complexity in analysis.complexity_scores.items():
        ca = coupling_map.get(mod_name)
        afferent = ca.afferent if ca else 0
        if complexity >= 30 and afferent >= 2:
            risk_score = complexity * afferent
            mod = next((m for m in analysis.modules if m.name == mod_name), None)
            file_path = mod.path if mod else mod_name.replace(".", "/") + ".py"
            findings.append({
                "id": "af-" + secrets.token_hex(3),
                "file": file_path,
                "line_start": 1,
                "line_end": 1,
                "severity": "high" if risk_score >= 100 else "medium",
                "category": "architecture",
                "title": f"Bottleneck module: {mod_name} (complexity {complexity}, {afferent} dependents)",
                "actual": (
                    f"{mod_name} has cyclomatic complexity {complexity} and is imported "
                    f"by {afferent} modules (risk score: {risk_score})"
                ),
                "expected": "High-traffic modules should have low complexity for safe modification",
                "suggestion_action": "refactor",
                "suggestion_detail": (
                    f"Split {mod_name} into smaller, focused modules. "
                    "Extract complex logic into helpers. Target complexity < 20."
                ),
                "confidence": 0.75,
                "tags": ["complexity", "bottleneck", "refactoring"],
            })

    # 4. Bloated modules (>= 15 definitions)
    for mod in analysis.modules:
        total_defs = len(mod.classes) + len(mod.functions)
        if total_defs >= 15:
            findings.append({
                "id": "af-" + secrets.token_hex(3),
                "file": mod.path,
                "line_start": 1,
                "line_end": 1,
                "severity": "medium" if total_defs < 25 else "high",
                "category": "architecture",
                "title": (
                    f"Bloated module: {mod.name} "
                    f"({len(mod.classes)} classes, {len(mod.functions)} functions)"
                ),
                "actual": (
                    f"{mod.name} has {total_defs} top-level definitions "
                    f"({len(mod.classes)} classes, {len(mod.functions)} functions, "
                    f"{mod.lines} lines)"
                ),
                "expected": "Modules should have a single responsibility; aim for < 10 top-level definitions",
                "suggestion_action": "refactor",
                "suggestion_detail": (
                    f"Split {mod.name} by responsibility: group related "
                    "classes/functions into sub-modules"
                ),
                "confidence": 0.7,
                "tags": ["bloated-module", "srp", "refactoring"],
            })

    if not findings:
        return 0

    try:
        writer.post_batch(findings)
        _log.info("Posted %d arch findings to swarm-kb for session %s", len(findings), session_id)
    except Exception as exc:
        _log.warning("Failed to post findings to swarm-kb: %s", exc)
        return 0

    return len(findings)


# ---------------------------------------------------------------------------
# Helpers -- generate code-informed proposals & critiques from scan results
# ---------------------------------------------------------------------------

def _top_n(mapping: dict[str, int | float], n: int = 5, reverse: bool = True) -> list[tuple[str, int | float]]:
    """Return the top-*n* items from *mapping* sorted by value."""
    return sorted(mapping.items(), key=lambda kv: kv[1], reverse=reverse)[:n]


def _find_circular_deps(dep_graph: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Find direct circular dependencies (A->B and B->A)."""
    all_names = set(dep_graph)
    cycles: list[tuple[str, str]] = []
    seen: set[frozenset[str]] = set()
    for mod, deps in dep_graph.items():
        for dep in deps:
            if dep in all_names and mod in dep_graph.get(dep, []):
                pair = frozenset((mod, dep))
                if pair not in seen:
                    seen.add(pair)
                    cycles.append((mod, dep))
    return cycles


def _coupling_map(analysis) -> dict[str, object]:
    """Build a name -> CouplingMetrics lookup from analysis."""
    return {c.module: c for c in analysis.coupling}


def _modules_with_many_defs(analysis, threshold: int = 8) -> list[tuple[str, int, int]]:
    """Modules with total classes+functions above *threshold*.

    Returns list of (name, num_classes, num_functions).
    """
    result = []
    for m in analysis.modules:
        total = len(m.classes) + len(m.functions)
        if total >= threshold:
            result.append((m.name, len(m.classes), len(m.functions)))
    result.sort(key=lambda t: t[1] + t[2], reverse=True)
    return result


def _generate_proposal_for_role(role, analysis, topic):
    """Produce a data-driven DesignProposal for *role* using *analysis*."""
    from .models import DesignProposal

    cmap = _coupling_map(analysis)
    name = role.name

    if name == "Simplicity Critic":
        return _proposal_simplicity(analysis, cmap, topic)
    elif name == "Modularity Expert":
        return _proposal_modularity(analysis, cmap, topic)
    elif name == "Reuse Finder":
        return _proposal_reuse(analysis, cmap, topic)
    elif name == "Scalability Critic":
        return _proposal_scalability(analysis, cmap, topic)
    elif name == "Trade-off Mediator":
        return _proposal_tradeoff(analysis, cmap, topic)

    # Fallback for unknown roles
    return DesignProposal(
        author=name,
        title=f"{name}: analysis of {topic}",
        description=f"Scanned {analysis.total_modules} modules ({analysis.total_lines} lines).",
        pros=["Provides a fresh perspective"],
        cons=["No specialised analysis heuristic for this role"],
        trade_offs=["General-purpose observation"],
    )


def _proposal_simplicity(analysis, cmap, topic):
    from .models import DesignProposal

    top_complex = _top_n(analysis.complexity_scores, 5)
    bloated = _modules_with_many_defs(analysis, threshold=6)

    desc_lines = [f"Scanned {analysis.total_modules} modules ({analysis.total_lines} lines)."]
    desc_lines.append("")
    if top_complex:
        desc_lines.append("**Most complex modules (cyclomatic):**")
        for mod, score in top_complex:
            desc_lines.append(f"  - {mod}: complexity {score}")
    if bloated:
        desc_lines.append("")
        desc_lines.append("**Modules with excessive definitions:**")
        for mod, nc, nf in bloated[:5]:
            desc_lines.append(f"  - {mod}: {nc} classes, {nf} functions")

    simplify_targets = [m for m, _ in top_complex[:3]]
    title = f"Simplify high-complexity modules"
    if simplify_targets:
        title = f"Simplify {simplify_targets[0]}" + (f" (+{len(simplify_targets)-1} more)" if len(simplify_targets) > 1 else "")

    pros = []
    if top_complex:
        worst, worst_score = top_complex[0]
        pros.append(f"Reducing complexity in {worst} (score {worst_score}) lowers bug risk")
    pros.append("Simpler modules are easier to test, review, and onboard into")
    if bloated:
        pros.append(f"{bloated[0][0]} has {bloated[0][1]+bloated[0][2]} definitions -- splitting improves readability")

    cons = [
        "Splitting may increase file count and import complexity",
        "Refactoring high-complexity code risks regressions without good test coverage",
    ]

    trade_offs = []
    if top_complex:
        trade_offs.append(f"Focus on top offenders first: {', '.join(m for m,_ in top_complex[:3])}")
    trade_offs.append("Accept moderate complexity in IO-heavy modules; prioritise logic-heavy ones")

    return DesignProposal(
        author="Simplicity Critic",
        title=title,
        description="\n".join(desc_lines),
        pros=pros,
        cons=cons,
        trade_offs=trade_offs,
    )


def _proposal_modularity(analysis, cmap, topic):
    from .models import DesignProposal

    # Identify high-coupling modules
    high_efferent = sorted(
        [(c.module, c.efferent, c.instability) for c in analysis.coupling if c.efferent > 0],
        key=lambda t: t[1], reverse=True,
    )[:5]

    high_instability = sorted(
        [(c.module, c.instability) for c in analysis.coupling if c.instability > 0.7 and (c.afferent + c.efferent) > 0],
        key=lambda t: t[1], reverse=True,
    )[:5]

    cycles = _find_circular_deps(analysis.dependency_graph)

    desc_lines = [f"Scanned {analysis.total_modules} modules."]
    desc_lines.append("")
    if high_efferent:
        desc_lines.append("**Highest outgoing coupling (Ce):**")
        for mod, ce, inst in high_efferent:
            desc_lines.append(f"  - {mod}: Ce={ce}, I={inst:.2f}")
    if high_instability:
        desc_lines.append("")
        desc_lines.append("**Most unstable modules (I > 0.7):**")
        for mod, inst in high_instability:
            desc_lines.append(f"  - {mod}: I={inst:.2f}")
    if cycles:
        desc_lines.append("")
        desc_lines.append("**Circular dependencies detected:**")
        for a, b in cycles[:5]:
            desc_lines.append(f"  - {a} <-> {b}")

    title = "Improve module boundaries"
    if cycles:
        title = f"Break {len(cycles)} circular dependency cycle(s)"
    elif high_efferent:
        title = f"Reduce coupling in {high_efferent[0][0]}"

    pros = []
    if cycles:
        pros.append(f"Breaking {len(cycles)} cycle(s) enables independent testing and deployment")
    if high_efferent:
        pros.append(f"{high_efferent[0][0]} depends on {high_efferent[0][1]} internal modules -- reducing this limits blast radius")
    pros.append("Cleaner boundaries make the codebase easier to navigate and maintain")

    cons = [
        "Introducing interfaces/abstractions adds indirection",
    ]
    if cycles:
        cons.append("Cycle-breaking may require new intermediate modules")

    trade_offs = []
    if high_instability:
        trade_offs.append(f"Stabilise {high_instability[0][0]} (I={high_instability[0][1]:.2f}) by reducing its outgoing deps")
    trade_offs.append("Accept some coupling for cohesive sub-systems; focus on cross-boundary coupling")

    return DesignProposal(
        author="Modularity Expert",
        title=title,
        description="\n".join(desc_lines),
        pros=pros,
        cons=cons,
        trade_offs=trade_offs,
    )


def _proposal_reuse(analysis, cmap, topic):
    from .models import DesignProposal

    # High afferent coupling = good abstraction candidates (widely imported)
    high_afferent = sorted(
        [(c.module, c.afferent) for c in analysis.coupling if c.afferent > 0],
        key=lambda t: t[1], reverse=True,
    )[:5]

    # Find modules with similar import patterns (potential duplication)
    import_sets: dict[str, set[str]] = {}
    for mod in analysis.modules:
        if mod.imports:
            import_sets[mod.name] = set(mod.imports)

    similar_pairs: list[tuple[str, str, int]] = []
    mod_names = list(import_sets.keys())
    for i, a in enumerate(mod_names):
        for b in mod_names[i + 1:]:
            overlap = import_sets[a] & import_sets[b]
            if len(overlap) >= 2:
                similar_pairs.append((a, b, len(overlap)))
    similar_pairs.sort(key=lambda t: t[2], reverse=True)

    desc_lines = [f"Scanned {analysis.total_modules} modules."]
    desc_lines.append("")
    if high_afferent:
        desc_lines.append("**Most-reused modules (highest afferent coupling):**")
        for mod, ca in high_afferent:
            desc_lines.append(f"  - {mod}: imported by {ca} other module(s)")
    if similar_pairs:
        desc_lines.append("")
        desc_lines.append("**Modules with overlapping imports (potential duplication):**")
        for a, b, count in similar_pairs[:5]:
            desc_lines.append(f"  - {a} and {b}: {count} shared imports")

    title = "Extract shared abstractions"
    if high_afferent:
        title = f"Promote {high_afferent[0][0]} as shared library"
    elif similar_pairs:
        title = f"Consolidate duplicated patterns in {similar_pairs[0][0]} and {similar_pairs[0][1]}"

    pros = []
    if high_afferent:
        pros.append(f"{high_afferent[0][0]} is already depended on by {high_afferent[0][1]} modules -- formalise its API")
    if similar_pairs:
        pros.append(f"{similar_pairs[0][0]} and {similar_pairs[0][1]} share {similar_pairs[0][2]} imports -- extracting common logic reduces drift")
    pros.append("Shared abstractions reduce total code volume and bug surface")

    cons = [
        "Premature abstraction can couple unrelated consumers",
        "Shared libraries become bottleneck dependencies if over-generalised",
    ]

    trade_offs = [
        "Only extract when duplication is real (3+ consumers), not speculative",
    ]
    if high_afferent:
        trade_offs.append(f"Keep {high_afferent[0][0]} stable (freeze its API) since many modules depend on it")

    return DesignProposal(
        author="Reuse Finder",
        title=title,
        description="\n".join(desc_lines),
        pros=pros,
        cons=cons,
        trade_offs=trade_offs,
    )


def _proposal_scalability(analysis, cmap, topic):
    from .models import DesignProposal

    # Hot spots: high complexity + high afferent = risky
    hot_spots: list[tuple[str, int, int]] = []
    for c in analysis.coupling:
        complexity = analysis.complexity_scores.get(c.module, 0)
        if complexity > 1 and c.afferent > 0:
            hot_spots.append((c.module, complexity, c.afferent))
    hot_spots.sort(key=lambda t: t[1] * t[2], reverse=True)

    # Large modules by line count
    large_modules = sorted(
        [(m.name, m.lines) for m in analysis.modules],
        key=lambda t: t[1], reverse=True,
    )[:5]

    desc_lines = [f"Scanned {analysis.total_modules} modules ({analysis.total_lines} lines)."]
    desc_lines.append("")
    if hot_spots:
        desc_lines.append("**Bottleneck modules (high complexity x high afferent):**")
        for mod, cx, ca in hot_spots[:5]:
            desc_lines.append(f"  - {mod}: complexity={cx}, depended-on-by={ca} (risk score {cx * ca})")
    if large_modules:
        desc_lines.append("")
        desc_lines.append("**Largest modules by line count:**")
        for mod, lines in large_modules:
            desc_lines.append(f"  - {mod}: {lines} lines")

    title = "Address scalability bottlenecks"
    if hot_spots:
        title = f"De-risk {hot_spots[0][0]} (complexity {hot_spots[0][1]} x {hot_spots[0][2]} dependents)"

    pros = []
    if hot_spots:
        worst = hot_spots[0]
        pros.append(f"{worst[0]} is the riskiest hot spot: complexity {worst[1]} with {worst[2]} dependents")
        pros.append("Reducing its complexity limits cascading failures when it changes")
    pros.append("Smaller, focused modules are easier to parallelise and cache")

    cons = [
        "Premature optimisation may add complexity without measurable gain",
    ]
    if large_modules:
        cons.append(f"Large files ({large_modules[0][0]}: {large_modules[0][1]} lines) are not always bottlenecks")

    trade_offs = []
    if hot_spots:
        trade_offs.append(f"Prioritise {hot_spots[0][0]} -- it has the highest risk score ({hot_spots[0][1]*hot_spots[0][2]})")
    trade_offs.append("Measure actual performance before splitting modules purely for scale")

    return DesignProposal(
        author="Scalability Critic",
        title=title,
        description="\n".join(desc_lines),
        pros=pros,
        cons=cons,
        trade_offs=trade_offs,
    )


def _proposal_tradeoff(analysis, cmap, topic):
    from .models import DesignProposal

    # Synthesise: modules appearing in multiple "problem" lists
    top_complex = {m for m, _ in _top_n(analysis.complexity_scores, 5)}
    high_efferent_set = {c.module for c in analysis.coupling if c.efferent > 0}
    high_afferent_set = {c.module for c in analysis.coupling if c.afferent > 0}
    bloated_set = {m for m, _, _ in _modules_with_many_defs(analysis, threshold=6)}

    # Count appearances across problem lists
    problem_counts: dict[str, int] = {}
    for name_set in (top_complex, high_efferent_set, bloated_set):
        for mod in name_set:
            problem_counts[mod] = problem_counts.get(mod, 0) + 1
    multi_problem = sorted(
        [(mod, cnt) for mod, cnt in problem_counts.items() if cnt >= 2],
        key=lambda t: t[1], reverse=True,
    )

    cycles = _find_circular_deps(analysis.dependency_graph)

    desc_lines = [f"Scanned {analysis.total_modules} modules ({analysis.total_lines} lines)."]
    desc_lines.append("")
    desc_lines.append("**Cross-cutting observations:**")
    if multi_problem:
        desc_lines.append("")
        desc_lines.append("Modules flagged by multiple perspectives:")
        for mod, cnt in multi_problem[:5]:
            flags = []
            if mod in top_complex:
                flags.append("high complexity")
            if mod in high_efferent_set:
                flags.append("high outgoing coupling")
            if mod in bloated_set:
                flags.append("many definitions")
            desc_lines.append(f"  - {mod}: {', '.join(flags)}")
    if cycles:
        desc_lines.append("")
        desc_lines.append(f"{len(cycles)} circular dependency cycle(s) need resolution.")
    if not multi_problem and not cycles:
        desc_lines.append("")
        desc_lines.append("No module appears in multiple problem categories -- codebase is relatively healthy.")

    title = "Pragmatic prioritisation"
    if multi_problem:
        title = f"Prioritise {multi_problem[0][0]} -- flagged by {multi_problem[0][1]} perspectives"

    pros = [
        "Focuses effort where multiple analyses agree, maximising ROI",
    ]
    if multi_problem:
        pros.append(f"{multi_problem[0][0]} appears in {multi_problem[0][1]} problem lists -- highest-leverage target")
    if cycles:
        pros.append(f"Cycle-breaking ({len(cycles)} cycle(s)) is low-controversy and high-value")

    cons = [
        "Compromise may leave some issues partially addressed",
        "Requires agreement on priority ordering",
    ]

    trade_offs = []
    if multi_problem:
        trade_offs.append(f"Start with {multi_problem[0][0]}: address complexity first, then coupling")
    if cycles:
        trade_offs.append("Break circular deps as quick wins before tackling larger refactors")
    trade_offs.append("Accept that not everything can be fixed at once; iterate")

    return DesignProposal(
        author="Trade-off Mediator",
        title=title,
        description="\n".join(desc_lines),
        pros=pros,
        cons=cons,
        trade_offs=trade_offs,
    )


def _generate_critique_for_role(role, proposal, analysis):
    """Produce a data-driven DesignCritique of *proposal* from *role*'s perspective."""
    from .models import DesignCritique, Verdict

    cmap = _coupling_map(analysis)
    name = role.name

    # Each role critiques from its own lens with concrete metrics
    if name == "Simplicity Critic":
        return _critique_simplicity(proposal, analysis, cmap)
    elif name == "Modularity Expert":
        return _critique_modularity(proposal, analysis, cmap)
    elif name == "Reuse Finder":
        return _critique_reuse(proposal, analysis, cmap)
    elif name == "Scalability Critic":
        return _critique_scalability(proposal, analysis, cmap)
    elif name == "Trade-off Mediator":
        return _critique_tradeoff(proposal, analysis, cmap)

    # Fallback
    return DesignCritique(
        proposal_id=proposal.id,
        critic=name,
        verdict=Verdict.MODIFY,
        reasoning=f"Reviewed from a general perspective; the proposal addresses {analysis.total_modules} modules.",
        suggested_changes=["Consider additional metrics"],
    )


def _critique_simplicity(proposal, analysis, cmap):
    from .models import DesignCritique, Verdict

    top_complex = _top_n(analysis.complexity_scores, 3)
    mentioned = [m for m, _ in top_complex if m in proposal.description]

    if mentioned:
        verdict = Verdict.SUPPORT
        reasoning = (f"Proposal correctly targets complex modules. "
                     f"Highest complexity: {top_complex[0][0]} (score {top_complex[0][1]}).")
        changes = ["Ensure refactoring does not introduce new abstractions that negate simplicity gains"]
    else:
        verdict = Verdict.MODIFY
        reasoning = (f"Proposal does not address the most complex modules. "
                     f"Top offender: {top_complex[0][0]} (complexity {top_complex[0][1]})." if top_complex
                     else "No complexity data available to evaluate.")
        changes = []
        if top_complex:
            changes.append(f"Include {top_complex[0][0]} in scope -- it has the highest complexity score")
        changes.append("Verify that proposed changes reduce total cyclomatic complexity")

    return DesignCritique(
        proposal_id=proposal.id,
        critic="Simplicity Critic",
        verdict=verdict,
        reasoning=reasoning,
        suggested_changes=changes,
    )


def _critique_modularity(proposal, analysis, cmap):
    from .models import DesignCritique, Verdict

    cycles = _find_circular_deps(analysis.dependency_graph)
    high_eff = sorted(
        [(c.module, c.efferent) for c in analysis.coupling if c.efferent > 0],
        key=lambda t: t[1], reverse=True,
    )[:3]

    issues = []
    if cycles:
        issues.append(f"{len(cycles)} circular dep(s) exist")
    if high_eff:
        issues.append(f"{high_eff[0][0]} has Ce={high_eff[0][1]}")

    if cycles and any(c[0] in proposal.description or c[1] in proposal.description for c in cycles):
        verdict = Verdict.SUPPORT
        reasoning = f"Proposal addresses circular dependencies. {'; '.join(issues)}."
    else:
        verdict = Verdict.MODIFY
        reasoning = f"Modularity concerns remain. {'; '.join(issues)}." if issues else "Coupling is low; proposal focus is reasonable."

    changes = []
    if cycles:
        changes.append(f"Address circular deps: {', '.join(f'{a}<->{b}' for a,b in cycles[:3])}")
    if high_eff:
        changes.append(f"Reduce outgoing coupling of {high_eff[0][0]} (Ce={high_eff[0][1]})")

    return DesignCritique(
        proposal_id=proposal.id,
        critic="Modularity Expert",
        verdict=verdict,
        reasoning=reasoning,
        suggested_changes=changes or ["Module boundaries look reasonable"],
    )


def _critique_reuse(proposal, analysis, cmap):
    from .models import DesignCritique, Verdict

    high_afferent = sorted(
        [(c.module, c.afferent) for c in analysis.coupling if c.afferent >= 2],
        key=lambda t: t[1], reverse=True,
    )[:3]

    if high_afferent:
        verdict = Verdict.MODIFY
        reasoning = (f"Reuse opportunity: {high_afferent[0][0]} is imported by "
                     f"{high_afferent[0][1]} modules. Its API should be stabilised before refactoring dependents.")
        changes = [
            f"Formalise {high_afferent[0][0]}'s public API before changing its consumers",
            "Check for duplicated utility functions across modules",
        ]
    else:
        verdict = Verdict.SUPPORT
        reasoning = "No widely-shared modules detected; proposal can proceed without reuse concerns."
        changes = ["Consider extracting common patterns if they emerge during implementation"]

    return DesignCritique(
        proposal_id=proposal.id,
        critic="Reuse Finder",
        verdict=verdict,
        reasoning=reasoning,
        suggested_changes=changes,
    )


def _critique_scalability(proposal, analysis, cmap):
    from .models import DesignCritique, Verdict

    hot_spots = []
    for c in analysis.coupling:
        cx = analysis.complexity_scores.get(c.module, 0)
        if cx > 1 and c.afferent > 0:
            hot_spots.append((c.module, cx, c.afferent))
    hot_spots.sort(key=lambda t: t[1] * t[2], reverse=True)

    if hot_spots:
        worst = hot_spots[0]
        if worst[0] in proposal.description:
            verdict = Verdict.SUPPORT
            reasoning = (f"Proposal targets {worst[0]}, the riskiest hot spot "
                         f"(complexity {worst[1]} x {worst[2]} dependents = risk {worst[1]*worst[2]}).")
        else:
            verdict = Verdict.MODIFY
            reasoning = (f"Proposal misses the biggest scalability risk: {worst[0]} "
                         f"(complexity {worst[1]} x {worst[2]} dependents = risk {worst[1]*worst[2]}).")
        changes = [
            f"Ensure {worst[0]} is addressed -- it is the highest-risk bottleneck",
            "Add performance tests for critical paths through high-fan-in modules",
        ]
    else:
        verdict = Verdict.SUPPORT
        reasoning = "No significant scalability hot spots detected in the scanned modules."
        changes = ["Continue monitoring as the codebase grows"]

    return DesignCritique(
        proposal_id=proposal.id,
        critic="Scalability Critic",
        verdict=verdict,
        reasoning=reasoning,
        suggested_changes=changes,
    )


def _critique_tradeoff(proposal, analysis, cmap):
    from .models import DesignCritique, Verdict

    top_complex = {m for m, _ in _top_n(analysis.complexity_scores, 5)}
    high_eff_set = {c.module for c in analysis.coupling if c.efferent > 0}
    bloated_set = {m for m, _, _ in _modules_with_many_defs(analysis, threshold=6)}

    problem_counts: dict[str, int] = {}
    for s in (top_complex, high_eff_set, bloated_set):
        for mod in s:
            problem_counts[mod] = problem_counts.get(mod, 0) + 1
    multi = [(m, c) for m, c in problem_counts.items() if c >= 2]

    if multi:
        verdict = Verdict.MODIFY
        targets = ", ".join(f"{m} ({c} flags)" for m, c in sorted(multi, key=lambda t: t[1], reverse=True)[:3])
        reasoning = f"Multiple analyses converge on: {targets}. Proposal should prioritise these."
        changes = [
            f"Address {multi[0][0]} first -- it appears in {multi[0][1]} problem categories",
            "Sequence changes to minimise disruption: quick wins first",
        ]
    else:
        verdict = Verdict.SUPPORT
        reasoning = "No single module dominates multiple problem categories. Proposal scope is reasonable."
        changes = ["Maintain the balanced approach"]

    return DesignCritique(
        proposal_id=proposal.id,
        critic="Trade-off Mediator",
        verdict=verdict,
        reasoning=reasoning,
        suggested_changes=changes,
    )


def create_mcp_server():
    """Create and configure the ArchSwarm MCP server."""
    from mcp.server.fastmcp import FastMCP, Context
    from contextlib import asynccontextmanager
    from collections.abc import AsyncIterator

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
        yield {}

    mcp = FastMCP("ArchSwarm", lifespan=lifespan)

    @mcp.tool(
        name="arch_analyze",
        description=(
            "Scan a project and return architecture metrics: "
            "modules, coupling, complexity, class hierarchy, dependencies."
        ),
    )
    def _arch_analyze(
        project_path: str,
        scope: str = "",
        ctx: Optional[Context] = None,
    ) -> str:
        from .code_scanner import scan_project, format_analysis
        import uuid

        analysis = scan_project(project_path, scope=scope or None)
        report = format_analysis(analysis)

        # Post findings to swarm-kb
        session_id = "analyze-" + uuid.uuid4().hex[:12]
        findings_count = _post_findings_to_kb(analysis, session_id)

        # Check for spec findings in swarm-kb
        spec_findings_count = 0
        try:
            from swarm_kb.config import SuiteConfig
            from swarm_kb.finding_reader import search_all_findings
            config = SuiteConfig.load()
            spec_findings = search_all_findings(config, tool="spec")
            spec_findings_count = len(spec_findings)
        except (ImportError, Exception):
            pass

        return json.dumps({
            "summary": {
                "total_modules": analysis.total_modules,
                "total_lines": analysis.total_lines,
            },
            "report": report,
            "findings_posted": findings_count,
            "spec_findings_available": spec_findings_count,
        })

    # ------------------------------------------------------------------
    # Local fallback debate (uses arch_swarm.debate when swarm-kb
    # is not installed)
    # ------------------------------------------------------------------

    def _arch_debate_local(project_path, topic, scope, analysis, context):
        from .agents import ALL_ROLES
        from .debate import DebateSession
        from .models import Verdict

        ds = DebateSession()
        ds.start_debate(topic=topic, context=context)

        for role in ALL_ROLES:
            proposal = _generate_proposal_for_role(role, analysis, topic)
            ds.add_proposal(proposal)

        for role in ALL_ROLES:
            for proposal in ds.session.proposals:
                if proposal.author == role.name:
                    continue
                critique = _generate_critique_for_role(role, proposal, analysis)
                ds.add_critique(critique)

        for role in ALL_ROLES:
            own_proposal = next(
                (p for p in ds.session.proposals if p.author == role.name), None
            )
            for proposal in ds.session.proposals:
                if own_proposal is not None and proposal.id == own_proposal.id:
                    ds.vote(agent=role.name, proposal_id=proposal.id, support=True)
                    continue
                own_critiques = [
                    c for c in ds.session.critiques
                    if c.critic == role.name and c.proposal_id == proposal.id
                ]
                support = bool(own_critiques and own_critiques[0].verdict == Verdict.SUPPORT)
                ds.vote(agent=role.name, proposal_id=proposal.id, support=support)

        decision = ds.resolve()
        transcript = ds.get_transcript()

        # Save to swarm-kb arch sessions directory
        # Validate session_id
        if ".." in ds.session.id or "/" in ds.session.id or "\\" in ds.session.id:
            return json.dumps({"error": "Invalid session_id"})
        session_dir = Path("~/.swarm-kb/arch/sessions").expanduser()
        session_dir.mkdir(parents=True, exist_ok=True)
        sess_dir = session_dir / ds.session.id
        sess_dir.mkdir(parents=True, exist_ok=True)

        try:
            (sess_dir / "transcript.md").write_text(transcript, encoding="utf-8")
            meta = {
                "session_id": ds.session.id,
                "topic": topic,
                "project_path": str(Path(project_path).resolve()),
                "decision": decision.title if decision else None,
                "status": decision.status.value if decision else None,
            }
            (sess_dir / "meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            _log.warning("Failed to save debate session: %s", exc)

        # Post findings to swarm-kb
        findings_count = _post_findings_to_kb(analysis, ds.session.id)

        return json.dumps({
            "session_id": ds.session.id,
            "topic": topic,
            "decision": decision.title if decision else None,
            "findings_posted": findings_count,
            "transcript": transcript,
        })

    # ------------------------------------------------------------------
    # Main debate tool
    # ------------------------------------------------------------------

    @mcp.tool(
        name="arch_debate",
        description=(
            "(Quick) Run an automated architecture debate with simulated agents. "
            "For real multi-agent debates, use orchestrate_debate instead."
        ),
    )
    def _arch_debate(
        project_path: str,
        topic: str,
        scope: str = "",
        ctx: Optional[Context] = None,
    ) -> str:
        from .agents import ALL_ROLES
        from .code_scanner import scan_project, format_analysis

        analysis = scan_project(project_path, scope=scope or None)
        context = format_analysis(analysis)

        # Use swarm-kb debate engine
        try:
            from swarm_kb.debate_engine import DebateEngine
            from swarm_kb.config import SuiteConfig
            config = SuiteConfig.load()
            engine = DebateEngine(config.debates_path / "active")
        except ImportError:
            # Fallback to local debate if swarm-kb not available
            return _arch_debate_local(project_path, topic, scope, analysis, context)

        debate = engine.start_debate(
            topic=topic, context=context,
            project_path=str(Path(project_path).resolve()),
            source_tool="arch", source_session="",
        )

        # Generate proposals from each role using analysis data
        for role in ALL_ROLES:
            proposal_data = _generate_proposal_for_role(role, analysis, topic)
            engine.propose(
                debate_id=debate.id,
                author=role.name,
                title=proposal_data.title,
                description=proposal_data.description,
                pros=proposal_data.pros,
                cons=proposal_data.cons,
                trade_offs=proposal_data.trade_offs,
            )

        # Generate critiques
        debate = engine.get_debate(debate.id)  # refresh
        for role in ALL_ROLES:
            for proposal in debate.proposals:
                if proposal.author == role.name:
                    continue
                critique_data = _generate_critique_for_role(role, proposal, analysis)
                engine.critique(
                    debate_id=debate.id,
                    proposal_id=proposal.id,
                    critic=role.name,
                    verdict=critique_data.verdict.value,
                    reasoning=critique_data.reasoning,
                    suggested_changes=critique_data.suggested_changes,
                )

        # Vote
        debate = engine.get_debate(debate.id)
        for role in ALL_ROLES:
            own_proposal = next((p for p in debate.proposals if p.author == role.name), None)
            for proposal in debate.proposals:
                support = (own_proposal is not None and proposal.id == own_proposal.id)
                if not support:
                    own_critiques = [c for c in debate.critiques
                                     if c.critic == role.name and c.proposal_id == proposal.id]
                    support = bool(own_critiques and own_critiques[0].verdict.value == "support")
                engine.vote(debate.id, role.name, proposal.id, support)

        # Resolve
        result = engine.resolve(debate.id)
        transcript = engine.get_transcript(debate.id)

        # Post findings to KB
        try:
            _post_findings_to_kb(analysis, debate.id)
        except Exception as exc:
            _log.warning("Failed to post findings: %s", exc)

        decision_dict = result.get("decision", {})
        decision_title = decision_dict.get("title") if isinstance(decision_dict, dict) else None

        return json.dumps({
            "session_id": debate.id,
            "topic": topic,
            "decision": decision_title,
            "transcript": transcript,
        })

    # ------------------------------------------------------------------
    # Orchestrated debate -- returns a plan for AI agents
    # ------------------------------------------------------------------

    @mcp.tool(
        name="orchestrate_debate",
        description=(
            "Plan a multi-agent architecture debate. Scans the project, starts a debate "
            "in swarm-kb, and returns step-by-step instructions for AI agents to propose, "
            "critique, vote, and resolve. Agents do the actual analysis."
        ),
    )
    def _orchestrate_debate(
        project_path: str,
        topic: str,
        scope: str = "",
        max_agents: int = 5,
        ctx: Optional[Context] = None,
    ) -> str:
        from .agents import ALL_ROLES
        from .code_scanner import scan_project, format_analysis

        # 1. Scan project
        analysis = scan_project(project_path, scope=scope or None)

        # Load spec report from swarm-kb if available
        spec_context = ""
        try:
            from swarm_kb.config import SuiteConfig
            from swarm_kb.finding_reader import search_all_findings
            config = SuiteConfig.load()
            spec_findings = search_all_findings(config, tool="spec")
            spec_reports = [f for f in spec_findings if f.get("category") == "spec-report"]
            if spec_reports:
                latest = spec_reports[-1]
                spec_context = latest.get("detail", "") or latest.get("suggestion_detail", "")
                if spec_context:
                    spec_context = f"\n\n## Hardware Specification Context\n\n{spec_context}\n"
        except (ImportError, Exception):
            pass

        context = format_analysis(analysis) + spec_context
        resolved_path = str(Path(project_path).resolve())

        # 2. Post findings to swarm-kb
        import uuid
        temp_session = "orch-" + uuid.uuid4().hex[:12]
        _post_findings_to_kb(analysis, temp_session)

        # 3. Start a debate via swarm-kb's DebateEngine
        debate_id: str
        try:
            from swarm_kb.debate_engine import DebateEngine
            from swarm_kb.config import SuiteConfig
            config = SuiteConfig.load()
            engine = DebateEngine(config.debates_path / "active")
            debate = engine.start_debate(
                topic=topic, context=context,
                project_path=resolved_path,
                source_tool="arch", source_session="",
            )
            debate_id = debate.id
        except ImportError:
            # If swarm-kb not available, return error
            return json.dumps({
                "error": "swarm-kb not installed. Install with: pip install swarm-kb",
                "fallback": "Use arch_debate() for automated debates without swarm-kb"
            })
        except Exception as exc:
            _log.warning("Failed to start debate in swarm-kb: %s", exc)
            return json.dumps({
                "error": f"Failed to start debate: {exc}",
                "fallback": "Use arch_debate() for automated debates"
            })

        # 4. Build context summary from analysis metrics
        context_parts = [
            f"{analysis.total_modules} modules",
            f"{analysis.total_lines} lines",
        ]
        top_complex = _top_n(analysis.complexity_scores, 3)
        if top_complex:
            for mod, score in top_complex:
                context_parts.append(f"{mod}: complexity {score}")
        top_coupling = sorted(
            [(c.module, c.efferent) for c in analysis.coupling if c.efferent > 0],
            key=lambda t: t[1], reverse=True,
        )[:3]
        if top_coupling:
            coupling_strs = [f"{mod} (Ce={ce})" for mod, ce in top_coupling]
            context_parts.append("Top coupling: " + ", ".join(coupling_strs))
        cycles = _find_circular_deps(analysis.dependency_graph)
        if cycles:
            context_parts.append(f"{len(cycles)} circular dependency cycle(s)")
        if spec_context:
            context_parts.append("Hardware spec constraints loaded from swarm-kb")
        context_summary = ". ".join(context_parts) + "."

        # 5. Select relevant expert roles (cap at max_agents)
        capped = max(min(max_agents, len(ALL_ROLES)), 2)
        selected_roles = ALL_ROLES[:capped]
        agents = []
        for role in selected_roles:
            agents.append({
                "role": role.name,
                "profile": role.name.lower().replace(" ", "-").replace("-", "_"),
                "focus_areas": list(role.focus_areas),
            })

        # 6. Build phased plan with per-agent instructions

        # Phase 1: Research & Propose
        phase1_instructions = []
        for agent in agents:
            phase1_instructions.append({
                "agent_role": agent["role"],
                "action": "propose",
                "description": (
                    f"Read the project files relevant to the topic. Analyze from a "
                    f"{agent['role'].lower()} perspective. Then call kb_propose("
                    f"debate_id='{debate_id}', author='{agent['role']}', "
                    f"title='...', description='...detailed analysis...', "
                    f"pros=[...], cons=[...]). Your proposal MUST reference specific "
                    f"files, metrics, and code patterns you found."
                ),
                "context": context_summary,
                "tools_to_use": ["kb_get_code_map", "kb_propose"],
            })

        # Phase 2: Critique & Challenge
        phase2_instructions = []
        for agent in agents:
            phase2_instructions.append({
                "agent_role": agent["role"],
                "action": "critique",
                "description": (
                    f"Call kb_get_debate('{debate_id}') to read all proposals. "
                    f"For each OTHER agent's proposal, call kb_critique(debate_id="
                    f"'{debate_id}', proposal_id=..., critic='{agent['role']}', "
                    f"verdict='support'|'oppose'|'modify', reasoning='...specific "
                    f"analysis...'). Your critique MUST address concrete claims in "
                    f"the proposal."
                ),
                "tools_to_use": ["kb_get_debate", "kb_critique"],
            })

        # Phase 3: Vote
        phase3_instructions = []
        for agent in agents:
            phase3_instructions.append({
                "agent_role": agent["role"],
                "action": "vote",
                "description": (
                    f"Call kb_get_debate('{debate_id}') to read the full debate "
                    f"state. For each proposal, call kb_vote(debate_id='{debate_id}', "
                    f"agent='{agent['role']}', proposal_id=..., support=True/False). "
                    f"Vote based on your analysis AND the critiques you've read."
                ),
                "tools_to_use": ["kb_get_debate", "kb_vote"],
            })

        # Phase 4: Resolve
        phase4_instructions = [{
            "action": "resolve",
            "description": (
                f"Call kb_resolve_debate('{debate_id}'). This tallies votes, picks "
                f"the winner, and saves the decision as an ADR in swarm-kb. Then "
                f"call kb_get_transcript('{debate_id}') to get the full debate "
                f"transcript."
            ),
            "tools_to_use": ["kb_resolve_debate", "kb_get_transcript"],
        }]

        phases = [
            {
                "phase": 1,
                "name": "Research & Propose",
                "description": (
                    "Each agent reads the project code, analyzes the topic from "
                    "their perspective, and submits a proposal."
                ),
                "instructions": phase1_instructions,
            },
            {
                "phase": 2,
                "name": "Critique & Challenge",
                "description": (
                    "Each agent reads all proposals and critiques them from their "
                    "expertise. Must reference specific weaknesses."
                ),
                "instructions": phase2_instructions,
            },
            {
                "phase": 3,
                "name": "Vote",
                "description": (
                    "Each agent votes on proposals based on the full debate "
                    "context (proposals + critiques)."
                ),
                "instructions": phase3_instructions,
            },
            {
                "phase": 4,
                "name": "Resolve",
                "description": (
                    "Tally votes and produce the final decision."
                ),
                "instructions": phase4_instructions,
            },
        ]

        summary = (
            f"Debate {debate_id} created. {len(agents)} agents will propose, "
            f"critique, and vote. Execute the 4 phases in order. Each agent "
            f"should READ CODE before proposing."
        )

        plan = {
            "debate_id": debate_id,
            "topic": topic,
            "project_path": resolved_path,
            "context_summary": context_summary,
            "agents": agents,
            "phases": phases,
            "summary": summary,
        }

        return json.dumps(plan)

    @mcp.tool(
        name="arch_list_sessions",
        description="List all ArchSwarm debate sessions.",
    )
    def _arch_list_sessions(ctx: Optional[Context] = None) -> str:
        session_dir = Path("~/.swarm-kb/arch/sessions").expanduser()
        result = []
        if session_dir.exists():
            for entry in sorted(session_dir.iterdir()):
                if entry.is_dir():
                    meta_path = entry / "meta.json"
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            result.append(meta)
                        except Exception:
                            result.append({"session_id": entry.name})
        return json.dumps(result)

    @mcp.tool(
        name="arch_get_transcript",
        description="Get the debate transcript for a previous session.",
    )
    def _arch_get_transcript(
        session_id: str,
        ctx: Optional[Context] = None,
    ) -> str:
        # Validate session_id
        if ".." in session_id or "/" in session_id or "\\" in session_id:
            return json.dumps({"error": "Invalid session_id"})

        # Try swarm-kb debate engine first
        try:
            from swarm_kb.debate_engine import DebateEngine
            from swarm_kb.config import SuiteConfig
            config = SuiteConfig.load()
            engine = DebateEngine(config.debates_path / "active")
            transcript = engine.get_transcript(session_id)
            if transcript:
                return transcript
        except (ImportError, Exception):
            pass
        # Fallback to file-based transcript
        session_dir = Path("~/.swarm-kb/arch/sessions").expanduser()
        md_file = session_dir / session_id / "transcript.md"
        if not md_file.exists():
            # Fallback to old format
            old_dir = Path(".archswarm_sessions")
            md_file = old_dir / f"{session_id}.md"
        if not md_file.exists():
            return json.dumps({"error": f"Session {session_id} not found"})
        return md_file.read_text(encoding="utf-8")

    return mcp
