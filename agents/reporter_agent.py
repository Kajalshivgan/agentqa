"""Reporter Agent: turns triage results into a Markdown run summary / bug-report draft."""

from __future__ import annotations

from agents.triage_agent import ENVIRONMENT, FLAKY, REGRESSION


def _analysis_lines(entry: dict, rule_kind: str) -> list[str]:
    """Triage findings for one test: the LLM's cause and action, and similar earlier failures."""
    lines = []
    llm = entry.get("llm") or {}
    if llm.get("error"):
        lines.append(f"- LLM analysis unavailable: {llm['error']}")
    elif llm:
        lines.append(f"- Probable cause ({llm.get('confidence', '?')} confidence): {llm.get('probable_cause', '')}")
        lines.append(f"- Suggested action: {llm.get('suggested_action', '')}")
        if llm.get("category") and llm["category"] != rule_kind:
            lines.append(f"- Note: the LLM would classify this as **{llm['category']}**, rules say {rule_kind}")
        if llm.get("similar_to"):
            lines.append(f"- Resembles: {llm['similar_to']}")
    for s in entry.get("similar", []):
        lines.append(f"- Seen before ({s['similarity']:.0%} similar, {s['count']}x, in {', '.join(s['tests'])}): "
                     f"{s['message']}")
    return lines


def build_report(final: dict[str, dict], classes: dict[str, str], flake_rates: dict[str, float],
                 analysis: dict[str, dict] | None = None) -> str:
    """`final` maps test name -> {status, suite, message, failed_step, log};
    `analysis` maps test name -> {similar: [...], llm: {...}} from the Triage Agent."""
    analysis = analysis or {}
    passed = [n for n, t in final.items() if t["status"] == "PASS"]
    lines = ["# AgentQA run summary", "",
             f"- Tests: {len(final)}, passed: {len(passed)}, failed: {len(final) - len(passed)}",
             f"- Regressions: {sum(c == REGRESSION for c in classes.values())}, "
             f"flaky: {sum(c == FLAKY for c in classes.values())}, "
             f"environment: {sum(c == ENVIRONMENT for c in classes.values())}", ""]
    bugs = [n for n, c in classes.items() if c == REGRESSION]
    if bugs:
        lines.append("## Bug reports (genuine regressions)")
        for name in bugs:
            t = final[name]
            step = t.get("failed_step") or {}
            lines += [f"### {name}", f"- Suite: `{t.get('suite', '')}`",
                      f"- Failed step: `{step.get('keyword', '?')} {' '.join(step.get('args', []))}`",
                      f"- Message: {t.get('message', '')}",
                      *_analysis_lines(analysis.get(name, {}), REGRESSION),
                      f"- Log: {t.get('log') or 'n/a'}", ""]
    others = [(n, c) for n, c in classes.items() if c in (FLAKY, ENVIRONMENT)]
    if others:
        lines.append("## Not filed as bugs")
        for name, kind in others:
            lines.append(f"- **{name}**: {kind} (historical flake rate {flake_rates.get(name, 0):.0%})")
            lines += [f"  {l}" for l in _analysis_lines(analysis.get(name, {}), kind)]
        lines.append("")
    return "\n".join(lines)
