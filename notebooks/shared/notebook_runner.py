"""Headless runner contract shared by skill notebook testers."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from typing import Any


class NotebookRunEngine:
    """Run a notebook prompt through an agent and populate debug state.

    The engine owns the common infer → parse → execute → debug_state flow. UI widgets stay
    responsible for collecting input and rendering the returned summary.
    """

    def __init__(
        self,
        *,
        make_agent: Callable[[], Any],
        debug_state: dict[str, Any],
        ollama_model: str = "",
        max_tokens: int = 512,
    ) -> None:
        self._make_agent = make_agent
        self._debug_state = debug_state
        self._ollama_model = ollama_model
        self._max_tokens = max_tokens

    def run_query(self, prompt: str, *, dry_run: bool = True) -> dict[str, Any]:
        agent = self._make_agent()
        use_mock = getattr(agent, "orchestrator", None) is None
        d: dict[str, Any] = {"user_input": prompt}
        t0 = time.perf_counter()
        try:
            system_msg, user_msg = agent.build_prompt(prompt)
            d["system_msg"] = system_msg
            d["user_msg"] = user_msg

            plan_acc, thinking_acc = "", ""
            t_llm = time.perf_counter()
            for kind, chunk in agent.infer_stream(
                prompt,
                use_mock=use_mock,
                ollama_model=self._ollama_model,
                max_tokens=self._max_tokens,
            ):
                if kind == "plan":
                    plan_acc += chunk
                else:
                    thinking_acc += chunk
            llm_ms = (time.perf_counter() - t_llm) * 1000
            d["raw_llm_output"] = plan_acc
            d["thinking"] = thinking_acc or getattr(agent, "last_thinking", "")

            try:
                plan = agent.parse_plan(agent._clean_json(plan_acc))
            except Exception as exc:  # noqa: BLE001
                d["llm_parse_error"] = str(exc)
                plan = {"plan": []}
            d["parsed_plan"] = plan

            exec_ms = 0.0
            results: list[dict[str, Any]] = []
            steps = plan.get("plan") or []
            first = steps[0].get("tool") if steps else None
            if steps and first not in ("clarify", "reject"):
                t_exec = time.perf_counter()
                results = agent.execute_plan(
                    plan, utterance=prompt, dry_run=dry_run, confirmed=not dry_run
                )
                exec_ms = (time.perf_counter() - t_exec) * 1000
                if results and results[0].get("tool") in ("clarify", "reject"):
                    first = results[0]["tool"]
                    steps = [{"tool": first, "args": results[0].get("args", {})}]
            d["execution_results"] = results

            total_ms = (time.perf_counter() - t0) * 1000
            d["timing"] = {"llm_ms": llm_ms, "exec_ms": exec_ms, "total_ms": total_ms}
            self._debug_state.clear()
            self._debug_state.update(d)
            return {
                "first_tool": first,
                "steps": steps,
                "results": results,
                "formatted": self._format_results(agent, results, dry_run),
                "dry_run": dry_run,
            }
        except Exception:  # noqa: BLE001
            d["error"] = traceback.format_exc()
            self._debug_state.clear()
            self._debug_state.update(d)
            raise

    @staticmethod
    def _format_results(agent: Any, results: list[dict], dry_run: bool) -> list[dict[str, str]]:
        formatter = getattr(agent, "result_formatter", None)
        if formatter is None or not results:
            return []
        try:
            return formatter(results, dry_run=dry_run)
        except Exception:  # noqa: BLE001
            return []
