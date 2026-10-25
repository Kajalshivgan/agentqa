"""Reporter Agent: turns triage results into a Markdown run summary / bug-report draft."""

from __future__ import annotations

from agents.triage_agent import ENVIRONMENT, FLAKY, REGRESSION


def build_report(final: dict[str, dict], classes: dict[str, str], flake_rates: dict[str, float]) -> str:
    """`final` maps test name -> {status, suite, message, failed_step, log}."""
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
                      f"- Log: {t.get('log') or 'n/a'}", ""]
    others = [(n, c) for n, c in classes.items() if c in (FLAKY, ENVIRONMENT)]
    if others:
        lines.append("## Not filed as bugs")
        for name, kind in others:
            lines.append(f"- **{name}**: {kind} (historical flake rate {flake_rates.get(name, 0):.0%})")
        lines.append("")
    return "\n".join(lines)
