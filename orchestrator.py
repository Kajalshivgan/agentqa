"""LangGraph orchestration: Executor -> Triage -> (retry Executor | Reporter).

    python orchestrator.py --device emulator-5554 --backend cloud
    python orchestrator.py --suite tests/robot/smoke_settings.robot     # one suite instead of all
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from agents import executor_agent, recorder_agent, reporter_agent, triage_agent
from agents.robot_mcp import core


class State(TypedDict, total=False):
    suites: list[str]
    device: str | None
    started: float
    attempt: int
    retry: dict[str, list[str]]        # suite path -> test names to re-run
    history: dict[str, list[str]]      # test name -> statuses per attempt
    final: dict[str, dict]             # test name -> latest result
    classes: dict[str, str]
    analysis: dict[str, dict]          # test name -> similar failures + LLM verdict
    report: str


def all_suites(directory: str = "tests/robot") -> list[str]:
    """Every suite except the `.rules.robot` baselines the recorder writes next to its output."""
    return [s["path"] for s in core.list_suites(directory) if not s["path"].endswith(".rules.robot")]


def build_graph(runner: Callable[..., dict[str, Any]] = core.run_suite,
                store: triage_agent.FlakinessStore | None = None, max_retries: int = 1,
                llm_client=None):
    store = store or triage_agent.FlakinessStore()

    def executor(state: State) -> State:
        attempt = state.get("attempt", 0)
        history = {k: list(v) for k, v in state.get("history", {}).items()}
        final = dict(state.get("final", {}))
        retry = state.get("retry") or {}
        targets = retry or {s: None for s in state.get("suites") or all_suites()}
        for suite, names in targets.items():
            result = executor_agent.execute(suite, state.get("device"), names, runner)
            if not result.get("tests"):   # runner failed before producing results
                name = f"<{suite}>"
                final[name] = {"name": name, "status": "FAIL", "suite": suite,
                               "message": result.get("error", "no output")}
                history.setdefault(name, []).append("FAIL")
                store.record(name, suite, "FAIL", final[name]["message"])
                continue
            for t in result["tests"]:
                history.setdefault(t["name"], []).append(t["status"])
                final[t["name"]] = {**t, "suite": suite, "log": result.get("log")}
                step = t.get("failed_step") or {}
                store.record(t["name"], suite, t["status"], t.get("message", ""),
                             f"{step.get('keyword', '')} {' '.join(step.get('args', []))}".strip())
        return {"history": history, "final": final, "attempt": attempt + 1, "retry": {},
                "started": state.get("started") or time.time()}

    def triage(state: State) -> State:
        classes, retry = {}, {}
        for name, test in state["final"].items():
            kind = triage_agent.classify(test, state["history"][name])
            if not kind:
                continue
            if kind == triage_agent.UNCONFIRMED:
                if state["attempt"] <= max_retries:
                    retry.setdefault(test["suite"], []).append(name)
                    continue
                kind = triage_agent.REGRESSION
            classes[name] = kind
        if retry:   # classification is not final yet; analyze once the retries are done
            return {"classes": classes, "retry": retry}
        analysis = {}
        for name, kind in classes.items():
            test = state["final"][name]
            similar = store.similar_failures(test.get("message", ""), before=state["started"])
            analysis[name] = {"similar": similar}
            if llm_client is not None:
                analysis[name]["llm"] = triage_agent.analyze(
                    {**test, "name": name}, kind, state["history"][name], similar,
                    store.flake_rate(name), llm_client)
        return {"classes": classes, "retry": {}, "analysis": analysis}

    def reporter(state: State) -> State:
        rates = {n: store.flake_rate(n) for n in state["final"]}
        return {"report": reporter_agent.build_report(state["final"], state["classes"], rates,
                                                      state.get("analysis"))}

    graph = StateGraph(State)
    graph.add_node("executor", executor)
    graph.add_node("triage", triage)
    graph.add_node("reporter", reporter)
    graph.add_edge(START, "executor")
    graph.add_edge("executor", "triage")
    graph.add_conditional_edges("triage", lambda s: "executor" if s.get("retry") else "reporter",
                                {"executor": "executor", "reporter": "reporter"})
    graph.add_edge("reporter", END)
    return graph.compile()


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentQA multi-agent regression run")
    parser.add_argument("--suite", action="append", help="suite to run (repeatable); default: all in tests/robot")
    parser.add_argument("--device")
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--backend", choices=["auto", "cloud", "local", "none"], default="auto",
                        help="LLM for failure analysis: cloud=Claude, local=Ollama, none=no LLM, "
                             "auto=cloud if ANTHROPIC_API_KEY is set")
    parser.add_argument("--out", default="results/report.md")
    args = parser.parse_args()
    recorder_agent.load_dotenv()
    graph = build_graph(max_retries=args.max_retries, llm_client=triage_agent.make_client(args.backend))
    state = graph.invoke({"suites": args.suite or all_suites(), "device": args.device})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(state["report"], encoding="utf-8")
    print(state["report"])
    return 1 if triage_agent.REGRESSION in state["classes"].values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
