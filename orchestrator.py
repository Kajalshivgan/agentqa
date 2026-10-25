"""LangGraph orchestration: Planner -> Executor -> Triage -> (retry Executor | Reporter).

    python orchestrator.py --changelog changelog.md --device emulator-5554
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from agents import executor_agent, planner_agent, reporter_agent, triage_agent
from agents.robot_mcp import core


class State(TypedDict, total=False):
    changelog: str
    device: str | None
    suites: list[str]
    attempt: int
    retry: dict[str, list[str]]        # suite path -> test names to re-run
    history: dict[str, list[str]]      # test name -> statuses per attempt
    final: dict[str, dict]             # test name -> latest result
    classes: dict[str, str]
    report: str


def build_graph(runner: Callable[..., dict[str, Any]] = core.run_suite,
                store: triage_agent.FlakinessStore | None = None, max_retries: int = 1,
                plan_fn: Callable[[str | None], list[str]] = planner_agent.plan):
    store = store or triage_agent.FlakinessStore()

    def planner(state: State) -> State:
        return {"suites": plan_fn(state.get("changelog")), "attempt": 0, "history": {}, "final": {},
                "retry": {}}

    def executor(state: State) -> State:
        retry = state.get("retry") or {}
        targets = retry or {s: None for s in state["suites"]}
        history = {k: list(v) for k, v in state["history"].items()}
        final = dict(state["final"])
        for suite, names in targets.items():
            result = executor_agent.execute(suite, state.get("device"), names, runner)
            if not result.get("tests"):   # runner failed before producing results
                name = f"<{suite}>"
                final[name] = {"status": "FAIL", "suite": suite, "message": result.get("error", "no output")}
                history.setdefault(name, []).append("FAIL")
                continue
            for t in result["tests"]:
                history.setdefault(t["name"], []).append(t["status"])
                final[t["name"]] = {**t, "suite": suite, "log": result.get("log")}
                store.record(t["name"], suite, t["status"])
        return {"history": history, "final": final, "attempt": state["attempt"] + 1}

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
        return {"classes": classes, "retry": retry}

    def reporter(state: State) -> State:
        rates = {n: store.flake_rate(n) for n in state["final"]}
        return {"report": reporter_agent.build_report(state["final"], state["classes"], rates)}

    graph = StateGraph(State)
    graph.add_node("planner", planner)
    graph.add_node("executor", executor)
    graph.add_node("triage", triage)
    graph.add_node("reporter", reporter)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "triage")
    graph.add_conditional_edges("triage", lambda s: "executor" if s.get("retry") else "reporter",
                                {"executor": "executor", "reporter": "reporter"})
    graph.add_edge("reporter", END)
    return graph.compile()


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentQA multi-agent regression run")
    parser.add_argument("--changelog", help="path to a changelog / requirements text file")
    parser.add_argument("--device")
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--out", default="results/report.md")
    args = parser.parse_args()
    changelog = Path(args.changelog).read_text(encoding="utf-8") if args.changelog else ""
    state = build_graph(max_retries=args.max_retries).invoke({"changelog": changelog, "device": args.device})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(state["report"], encoding="utf-8")
    print(state["report"])
    return 1 if triage_agent.REGRESSION in state["classes"].values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
